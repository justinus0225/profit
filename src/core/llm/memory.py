"""에이전트 메모리 관리 (ARCHITECTURE.md P11, Section 10.7).

단기 메모리 (Redis, TTL 24h):
- 최근 의사결정 이력, 진행 중 작업 컨텍스트
- 에이전트당 최대 50개 엔트리

장기 메모리 (TimescaleDB + pgvector):
- 과거 매매 기록, 전략 성과, 시장 패턴
- RAG 검색용 임베딩 벡터 저장
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis

from src.core.config import LLMMemoryConfig

logger = logging.getLogger(__name__)

# Redis 키 패턴
SHORT_TERM_KEY = "agent:memory:short:{agent_type}"
SHORT_TERM_INDEX = "agent:memory:index:{agent_type}"


@dataclass
class MemoryEntry:
    """메모리 엔트리."""

    key: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    agent_type: str = ""


class AgentMemoryManager:
    """에이전트 단기/장기 메모리 관리자.

    단기 메모리: Redis (TTL 24시간, 최대 50개)
    장기 메모리: TimescaleDB + pgvector (RAG 모듈에서 관리)
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        config: LLMMemoryConfig,
    ) -> None:
        self._redis = redis_client
        self._config = config

    # ── 단기 메모리 (Redis) ──

    async def store_short_term(
        self,
        agent_type: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """단기 메모리에 엔트리를 저장한다.

        Args:
            agent_type: 에이전트 유형 (예: "quant", "analyst")
            key: 메모리 키 (예: "decision:BTC:20240301")
            content: 메모리 내용
            metadata: 추가 메타데이터
        """
        ttl_seconds = self._config.short_term_ttl_hours * 3600
        redis_key = SHORT_TERM_KEY.format(agent_type=agent_type)
        index_key = SHORT_TERM_INDEX.format(agent_type=agent_type)

        entry = {
            "key": key,
            "content": content,
            "metadata": metadata or {},
            "timestamp": time.time(),
        }

        # Hash로 저장 (key → JSON)
        await self._redis.hset(redis_key, key, json.dumps(entry, default=str))
        await self._redis.expire(redis_key, ttl_seconds)

        # 인덱스 (정렬용 Sorted Set, score=timestamp)
        await self._redis.zadd(index_key, {key: time.time()})
        await self._redis.expire(index_key, ttl_seconds)

        # 최대 엔트리 초과 시 오래된 항목 제거
        await self._trim_short_term(agent_type)

    async def get_short_term(
        self,
        agent_type: str,
        key: str,
    ) -> MemoryEntry | None:
        """단기 메모리에서 특정 엔트리를 조회한다."""
        redis_key = SHORT_TERM_KEY.format(agent_type=agent_type)
        raw = await self._redis.hget(redis_key, key)
        if not raw:
            return None
        data = json.loads(raw)
        return MemoryEntry(
            key=data["key"],
            content=data["content"],
            metadata=data.get("metadata", {}),
            timestamp=data.get("timestamp", 0),
            agent_type=agent_type,
        )

    async def get_recent_short_term(
        self,
        agent_type: str,
        limit: int | None = None,
    ) -> list[MemoryEntry]:
        """최근 단기 메모리를 시간역순으로 조회한다."""
        redis_key = SHORT_TERM_KEY.format(agent_type=agent_type)
        index_key = SHORT_TERM_INDEX.format(agent_type=agent_type)
        max_entries = limit or self._config.short_term_max_entries

        # 최신순으로 키 조회
        keys = await self._redis.zrevrange(index_key, 0, max_entries - 1)
        if not keys:
            return []

        entries: list[MemoryEntry] = []
        for k in keys:
            raw = await self._redis.hget(redis_key, k)
            if raw:
                data = json.loads(raw)
                entries.append(MemoryEntry(
                    key=data["key"],
                    content=data["content"],
                    metadata=data.get("metadata", {}),
                    timestamp=data.get("timestamp", 0),
                    agent_type=agent_type,
                ))
        return entries

    async def delete_short_term(self, agent_type: str, key: str) -> None:
        """단기 메모리에서 특정 엔트리를 삭제한다."""
        redis_key = SHORT_TERM_KEY.format(agent_type=agent_type)
        index_key = SHORT_TERM_INDEX.format(agent_type=agent_type)
        await self._redis.hdel(redis_key, key)
        await self._redis.zrem(index_key, key)

    async def clear_short_term(self, agent_type: str) -> None:
        """에이전트의 전체 단기 메모리를 삭제한다."""
        redis_key = SHORT_TERM_KEY.format(agent_type=agent_type)
        index_key = SHORT_TERM_INDEX.format(agent_type=agent_type)
        await self._redis.delete(redis_key, index_key)

    async def _trim_short_term(self, agent_type: str) -> None:
        """최대 엔트리 수를 초과하면 오래된 항목을 제거한다."""
        index_key = SHORT_TERM_INDEX.format(agent_type=agent_type)
        redis_key = SHORT_TERM_KEY.format(agent_type=agent_type)
        max_entries = self._config.short_term_max_entries

        count = await self._redis.zcard(index_key)
        if count <= max_entries:
            return

        # 오래된 항목 삭제
        to_remove = count - max_entries
        old_keys = await self._redis.zrange(index_key, 0, to_remove - 1)
        for k in old_keys:
            await self._redis.hdel(redis_key, k)
            await self._redis.zrem(index_key, k)

        logger.debug(
            "Memory trimmed: agent=%s, removed=%d, remaining=%d",
            agent_type,
            len(old_keys),
            max_entries,
        )

    async def get_memory_stats(self, agent_type: str) -> dict[str, Any]:
        """에이전트 메모리 통계를 반환한다."""
        index_key = SHORT_TERM_INDEX.format(agent_type=agent_type)
        count = await self._redis.zcard(index_key)
        return {
            "agent_type": agent_type,
            "short_term_count": count,
            "short_term_max": self._config.short_term_max_entries,
            "ttl_hours": self._config.short_term_ttl_hours,
        }
