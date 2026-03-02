"""LLM 컨텍스트 윈도우 관리 (ARCHITECTURE.md P11, Section 10.7).

프롬프트 조합 엔진:
1. 시스템 프롬프트 (고정, 에이전트 역할 정의)
2. 단기 메모리 (Redis, 최근 의사결정)
3. RAG 검색 결과 (pgvector, 장기 메모리)
4. 현재 작업 데이터 (실시간 시장 데이터)

에이전트별 max_input_tokens 제한 내에서 조합한다.
초과 시 3단계 압축 전략을 적용한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.core.config import LLMMemoryConfig
from src.core.llm.client import LLMClient, LLMResponse, Message, Role

logger = logging.getLogger(__name__)

# 토큰 추정: 영어 ~4자/토큰, 한국어 ~2자/토큰 (보수적 추정)
CHARS_PER_TOKEN = 3


@dataclass
class ContextBlock:
    """프롬프트 구성 블록."""

    label: str  # "system" | "short_term" | "rag" | "task"
    content: str
    priority: int  # 높을수록 중요 (system=100, task=90, rag=50, memory=30)
    estimated_tokens: int = 0

    def __post_init__(self) -> None:
        if self.estimated_tokens == 0:
            self.estimated_tokens = estimate_tokens(self.content)


@dataclass
class ContextBuildResult:
    """컨텍스트 조합 결과."""

    messages: list[Message]
    total_tokens: int
    blocks_used: list[str]  # 사용된 블록 라벨
    compressed: bool = False
    compression_stage: int = 0  # 0=미적용, 1=트리밍, 2=요약, 3=RAG축소


def estimate_tokens(text: str) -> int:
    """텍스트의 대략적 토큰 수를 추정한다."""
    return max(1, len(text) // CHARS_PER_TOKEN)


class ContextManager:
    """LLM 컨텍스트 윈도우 관리자.

    에이전트별 max_input_tokens 제한 내에서 프롬프트를 조합하고,
    초과 시 3단계 압축 전략을 적용한다.
    """

    def __init__(
        self,
        config: LLMMemoryConfig,
        compression_client: LLMClient | None = None,
    ) -> None:
        self._config = config
        self._compression_client = compression_client

    def get_max_tokens(self, agent_type: str) -> int:
        """에이전트별 최대 입력 토큰 수를 반환한다."""
        tokens_config = self._config.agent_max_input_tokens
        return getattr(tokens_config, agent_type, 4096)

    async def build_context(
        self,
        agent_type: str,
        system_prompt: str,
        task_content: str,
        short_term_memories: list[str] | None = None,
        rag_results: list[str] | None = None,
    ) -> ContextBuildResult:
        """에이전트 프롬프트를 조합한다.

        Args:
            agent_type: 에이전트 유형
            system_prompt: 시스템 프롬프트
            task_content: 현재 작업 데이터
            short_term_memories: 단기 메모리 목록
            rag_results: RAG 검색 결과 목록

        Returns:
            ContextBuildResult: 조합된 메시지 및 메타 정보
        """
        max_tokens = self.get_max_tokens(agent_type)

        # 블록 구성
        blocks: list[ContextBlock] = []

        # [1] 시스템 프롬프트 (필수, 최고 우선순위)
        blocks.append(ContextBlock(
            label="system",
            content=system_prompt,
            priority=100,
        ))

        # [4] 현재 작업 (필수, 높은 우선순위)
        blocks.append(ContextBlock(
            label="task",
            content=task_content,
            priority=90,
        ))

        # [3] RAG 검색 결과 (선택)
        if rag_results:
            rag_text = "\n\n---\n\n".join(rag_results)
            blocks.append(ContextBlock(
                label="rag",
                content=rag_text,
                priority=50,
            ))

        # [2] 단기 메모리 (선택)
        if short_term_memories:
            memory_text = "\n".join(
                f"- {m}" for m in short_term_memories
            )
            blocks.append(ContextBlock(
                label="short_term",
                content=memory_text,
                priority=30,
            ))

        # 총 토큰 계산
        total_tokens = sum(b.estimated_tokens for b in blocks)

        # 초과 시 3단계 압축
        compression_stage = 0
        compressed = False

        if total_tokens > max_tokens:
            blocks, compression_stage = await self._compress(
                blocks, max_tokens, agent_type
            )
            total_tokens = sum(b.estimated_tokens for b in blocks)
            compressed = True

        # 메시지 조합
        messages = self._blocks_to_messages(blocks)

        return ContextBuildResult(
            messages=messages,
            total_tokens=total_tokens,
            blocks_used=[b.label for b in blocks],
            compressed=compressed,
            compression_stage=compression_stage,
        )

    async def _compress(
        self,
        blocks: list[ContextBlock],
        max_tokens: int,
        agent_type: str,
    ) -> tuple[list[ContextBlock], int]:
        """3단계 압축 전략을 적용한다."""
        total = sum(b.estimated_tokens for b in blocks)

        # Stage 1: 단기 메모리 트리밍
        if total > max_tokens:
            blocks = self._trim_memory(blocks, max_tokens)
            total = sum(b.estimated_tokens for b in blocks)
            if total <= max_tokens:
                logger.debug(
                    "Compression Stage 1 (trim): %s tokens=%d/%d",
                    agent_type, total, max_tokens,
                )
                return blocks, 1

        # Stage 2: 단기 메모리 요약 (압축 클라이언트 필요)
        if total > max_tokens and self._config.compression_enabled:
            blocks = await self._summarize_memory(blocks, max_tokens)
            total = sum(b.estimated_tokens for b in blocks)
            if total <= max_tokens:
                logger.debug(
                    "Compression Stage 2 (summarize): %s tokens=%d/%d",
                    agent_type, total, max_tokens,
                )
                return blocks, 2

        # Stage 3: RAG 결과 축소
        if total > max_tokens:
            blocks = self._reduce_rag(blocks, max_tokens)
            total = sum(b.estimated_tokens for b in blocks)
            logger.debug(
                "Compression Stage 3 (reduce RAG): %s tokens=%d/%d",
                agent_type, total, max_tokens,
            )
            return blocks, 3

        return blocks, 0

    def _trim_memory(
        self,
        blocks: list[ContextBlock],
        max_tokens: int,
    ) -> list[ContextBlock]:
        """Stage 1: 단기 메모리 항목을 오래된 것부터 제거."""
        result = []
        for b in blocks:
            if b.label == "short_term":
                lines = b.content.split("\n")
                # 최신 항목만 유지 (뒤에서부터)
                while lines and sum(bl.estimated_tokens for bl in result) + estimate_tokens("\n".join(lines)) > max_tokens:
                    lines.pop(0)  # 가장 오래된 것 제거
                if lines:
                    result.append(ContextBlock(
                        label="short_term",
                        content="\n".join(lines),
                        priority=b.priority,
                    ))
            else:
                result.append(b)
        return result

    async def _summarize_memory(
        self,
        blocks: list[ContextBlock],
        max_tokens: int,
    ) -> list[ContextBlock]:
        """Stage 2: 경량 모델로 단기 메모리를 요약."""
        if not self._compression_client:
            return blocks

        result = []
        for b in blocks:
            if b.label == "short_term" and b.estimated_tokens > 200:
                try:
                    summary_prompt = (
                        "Summarize the following agent decision history concisely "
                        "in bullet points. Keep only the most important decisions "
                        "and their outcomes:\n\n" + b.content
                    )
                    response: LLMResponse = await self._compression_client.chat(
                        [Message(role=Role.USER, content=summary_prompt)],
                        model=self._config.compression_model,
                        max_tokens=500,
                    )
                    result.append(ContextBlock(
                        label="short_term",
                        content=response.content,
                        priority=b.priority,
                    ))
                    logger.debug(
                        "Memory summarized: %d → %d tokens",
                        b.estimated_tokens,
                        estimate_tokens(response.content),
                    )
                except Exception:
                    logger.warning("Memory summarization failed, using trimmed version")
                    result.append(b)
            else:
                result.append(b)
        return result

    def _reduce_rag(
        self,
        blocks: list[ContextBlock],
        max_tokens: int,
    ) -> list[ContextBlock]:
        """Stage 3: RAG 결과 수를 줄인다 (Top-K → K/2 → K/4)."""
        result = []
        for b in blocks:
            if b.label == "rag":
                sections = b.content.split("\n\n---\n\n")
                # 결과 수를 절반으로 줄임
                reduced = sections[: max(1, len(sections) // 2)]
                if reduced:
                    result.append(ContextBlock(
                        label="rag",
                        content="\n\n---\n\n".join(reduced),
                        priority=b.priority,
                    ))
            else:
                result.append(b)
        return result

    def _blocks_to_messages(self, blocks: list[ContextBlock]) -> list[Message]:
        """블록을 LLM 메시지 리스트로 변환한다."""
        messages: list[Message] = []

        system_content = ""
        user_content_parts: list[str] = []

        for b in blocks:
            if b.label == "system":
                system_content = b.content
            elif b.label == "short_term":
                user_content_parts.append(
                    f"[Recent Decisions]\n{b.content}"
                )
            elif b.label == "rag":
                user_content_parts.append(
                    f"[Relevant Past Experience]\n{b.content}"
                )
            elif b.label == "task":
                user_content_parts.append(
                    f"[Current Task]\n{b.content}"
                )

        if system_content:
            messages.append(Message(role=Role.SYSTEM, content=system_content))

        if user_content_parts:
            messages.append(Message(
                role=Role.USER,
                content="\n\n".join(user_content_parts),
            ))

        return messages
