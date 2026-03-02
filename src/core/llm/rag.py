"""RAG 파이프라인 (ARCHITECTURE.md P11, Section 10.7).

장기 메모리 검색:
1. 쿼리 생성 (현재 작업 → 검색 벡터)
2. pgvector 유사도 검색 (코사인 유사도)
3. 결과 포맷팅 → 프롬프트 조합 엔진에 전달

테이블: agent_memory_embeddings
검색: SELECT * FROM agent_memory_embeddings
       WHERE agent_type = '{agent}'
       ORDER BY embedding <=> query_vector
       LIMIT {top_k}
"""

from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.core.config import LLMMemoryConfig
from src.core.llm.client import EmbeddingResult, LLMClient

logger = logging.getLogger(__name__)


@dataclass
class RAGResult:
    """RAG 검색 결과."""

    content: str
    similarity: float
    agent_type: str
    memory_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass
class RAGSearchResult:
    """RAG 검색 통합 결과."""

    query: str
    results: list[RAGResult]
    total_tokens: int = 0
    search_time_ms: float = 0.0


class RAGPipeline:
    """RAG (Retrieval-Augmented Generation) 파이프라인.

    pgvector 기반 유사도 검색으로 관련 과거 경험을 검색하고
    프롬프트에 주입한다.

    사용법:
        rag = RAGPipeline(llm_client, config)
        results = await rag.search(agent_type, query, session)
        context_texts = rag.format_results(results)
    """

    def __init__(
        self,
        llm_client: LLMClient,
        config: LLMMemoryConfig,
    ) -> None:
        self._llm = llm_client
        self._config = config

    async def store(
        self,
        session: Any,  # AsyncSession
        agent_type: str,
        content: str,
        memory_type: str = "decision",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """장기 메모리를 임베딩하여 저장한다.

        Args:
            session: SQLAlchemy AsyncSession
            agent_type: 에이전트 유형
            content: 저장할 텍스트
            memory_type: 메모리 유형 ("decision", "market_pattern", "trade_result")
            metadata: 추가 메타데이터
        """
        if not self._config.rag_enabled:
            return

        # 임베딩 생성
        embedding_result: EmbeddingResult = await self._llm.embed(content)
        embedding_bytes = _vector_to_bytes(embedding_result.vector)

        # DB 저장
        from src.data.models.memory import AgentMemoryEmbedding

        record = AgentMemoryEmbedding(
            agent_type=agent_type,
            memory_type=memory_type,
            content=content,
            content_timestamp=datetime.now(tz=timezone.utc),
            embedding=embedding_bytes,
            metadata_=metadata or {},
        )
        session.add(record)
        await session.flush()

        logger.debug(
            "RAG stored: agent=%s type=%s tokens=%d dim=%d",
            agent_type,
            memory_type,
            embedding_result.input_tokens,
            embedding_result.dimensions,
        )

    async def search(
        self,
        agent_type: str,
        query: str,
        session: Any,  # AsyncSession
        *,
        top_k: int | None = None,
        memory_type: str | None = None,
    ) -> RAGSearchResult:
        """장기 메모리에서 유사한 경험을 검색한다.

        Args:
            agent_type: 에이전트 유형
            query: 검색 쿼리
            session: SQLAlchemy AsyncSession
            top_k: 반환할 결과 수 (기본: config.rag_top_k)
            memory_type: 필터할 메모리 유형 (선택)

        Returns:
            RAGSearchResult: 검색 결과
        """
        import time as _time

        if not self._config.rag_enabled:
            return RAGSearchResult(query=query, results=[])

        start = _time.monotonic()
        k = top_k or self._config.rag_top_k
        threshold = self._config.rag_similarity_threshold

        # 쿼리 임베딩
        query_embedding: EmbeddingResult = await self._llm.embed(query)

        # pgvector 유사도 검색 (코사인 거리)
        # NOTE: pgvector가 설치된 환경에서는 <=> 연산자를 사용.
        # 현재는 application-level 유사도 계산으로 폴백.
        from sqlalchemy import select, text
        from src.data.models.memory import AgentMemoryEmbedding

        stmt = (
            select(AgentMemoryEmbedding)
            .where(AgentMemoryEmbedding.agent_type == agent_type)
        )
        if memory_type:
            stmt = stmt.where(AgentMemoryEmbedding.memory_type == memory_type)

        result = await session.execute(stmt)
        records = result.scalars().all()

        # Application-level 코사인 유사도 계산
        scored_results: list[tuple[float, Any]] = []
        query_vec = query_embedding.vector

        for record in records:
            if record.embedding is None:
                continue
            record_vec = _bytes_to_vector(record.embedding)
            similarity = _cosine_similarity(query_vec, record_vec)
            if similarity >= threshold:
                scored_results.append((similarity, record))

        # 유사도 내림차순 정렬, Top-K
        scored_results.sort(key=lambda x: x[0], reverse=True)
        top_results = scored_results[:k]

        rag_results = [
            RAGResult(
                content=rec.content,
                similarity=round(sim, 4),
                agent_type=rec.agent_type,
                memory_type=rec.memory_type,
                metadata=rec.metadata_ or {},
                created_at=rec.content_timestamp,
            )
            for sim, rec in top_results
        ]

        elapsed = (_time.monotonic() - start) * 1000

        logger.debug(
            "RAG search: agent=%s query_len=%d results=%d/%d time=%.1fms",
            agent_type,
            len(query),
            len(rag_results),
            len(records),
            elapsed,
        )

        return RAGSearchResult(
            query=query,
            results=rag_results,
            search_time_ms=round(elapsed, 1),
        )

    def format_results(self, search_result: RAGSearchResult) -> list[str]:
        """RAG 결과를 프롬프트에 삽입할 텍스트 리스트로 변환한다."""
        formatted: list[str] = []
        for r in search_result.results:
            date_str = (
                r.created_at.strftime("%Y-%m-%d %H:%M")
                if r.created_at
                else "unknown"
            )
            text = (
                f"[{r.memory_type}] ({date_str}, similarity={r.similarity})\n"
                f"{r.content}"
            )
            formatted.append(text)
        return formatted


# ── 유틸리티 함수 ──


def _vector_to_bytes(vector: list[float]) -> bytes:
    """float 리스트를 바이트로 변환한다."""
    return struct.pack(f"{len(vector)}f", *vector)


def _bytes_to_vector(data: bytes) -> list[float]:
    """바이트를 float 리스트로 변환한다."""
    count = len(data) // 4  # float32 = 4 bytes
    return list(struct.unpack(f"{count}f", data))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """코사인 유사도를 계산한다."""
    import math

    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
