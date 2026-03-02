"""P.R.O.F.I.T. 테스트 공통 Fixtures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.config import ConfigManager, ProfitConfig
from src.core.llm.client import (
    AnalysisResult,
    EmbeddingResult,
    LLMClient,
    LLMResponse,
    Message,
    Role,
)


# ── Fake LLM Client ──


class FakeLLMClient(LLMClient):
    """테스트용 LLM 클라이언트."""

    def __init__(self) -> None:
        self._chat_responses: list[str] = ["Test response"]
        self._embed_vector: list[float] = [0.1] * 768

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        content = self._chat_responses[0] if self._chat_responses else "ok"
        return LLMResponse(
            content=content,
            model=model or "fake-model",
            provider="fake",
            input_tokens=10,
            output_tokens=5,
        )

    async def analyze(
        self,
        prompt: str,
        context: str = "",
        *,
        model: str | None = None,
    ) -> AnalysisResult:
        resp = await self.chat([Message(Role.USER, prompt)])
        return AnalysisResult(content=resp.content, confidence=0.8, response=resp)

    async def embed(
        self,
        text: str,
        *,
        model: str | None = None,
    ) -> EmbeddingResult:
        return EmbeddingResult(
            vector=list(self._embed_vector),
            model=model or "fake-embed",
            provider="fake",
            dimensions=len(self._embed_vector),
            input_tokens=len(text) // 4,
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        yield "streamed"

    async def health_check(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        return "fake"


# ── Fake Redis ──


class FakeRedis:
    """테스트용 인메모리 Redis."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._hash_store: dict[str, dict[str, str]] = {}
        self._zset_store: dict[str, list[tuple[str, float]]] = {}
        self._list_store: dict[str, list[str]] = {}
        self._expiry: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(
        self,
        key: str,
        value: Any,
        *,
        nx: bool = False,
        px: int | None = None,
        ex: int | None = None,
    ) -> bool | None:
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            removed = False
            if k in self._store:
                del self._store[k]
                removed = True
            if k in self._hash_store:
                del self._hash_store[k]
                removed = True
            if k in self._zset_store:
                del self._zset_store[k]
                removed = True
            if k in self._list_store:
                del self._list_store[k]
                removed = True
            if removed:
                count += 1
        return count

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        # 간단한 Lua 스크립트 시뮬레이션 (release lock)
        if numkeys >= 1:
            key = args[0]
            expected = args[1] if len(args) > 1 else None
            stored = self._store.get(key)
            if stored == expected:
                self._store.pop(key, None)
                return 1
        return 0

    async def hset(self, key: str, field: str, value: str) -> int:
        if key not in self._hash_store:
            self._hash_store[key] = {}
        is_new = field not in self._hash_store[key]
        self._hash_store[key][field] = value
        return 1 if is_new else 0

    async def hget(self, key: str, field: str) -> str | None:
        return self._hash_store.get(key, {}).get(field)

    async def hdel(self, key: str, *fields: str) -> int:
        count = 0
        if key in self._hash_store:
            for f in fields:
                if f in self._hash_store[key]:
                    del self._hash_store[key][f]
                    count += 1
        return count

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hash_store.get(key, {}))

    async def hlen(self, key: str) -> int:
        return len(self._hash_store.get(key, {}))

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        if key not in self._zset_store:
            self._zset_store[key] = []
        added = 0
        for member, score in mapping.items():
            # Update existing or add new
            found = False
            for i, (m, _s) in enumerate(self._zset_store[key]):
                if m == member:
                    self._zset_store[key][i] = (member, score)
                    found = True
                    break
            if not found:
                self._zset_store[key].append((member, score))
                added += 1
        self._zset_store[key].sort(key=lambda x: x[1])
        return added

    async def zrem(self, key: str, *members: str) -> int:
        count = 0
        if key in self._zset_store:
            before = len(self._zset_store[key])
            self._zset_store[key] = [
                (m, s) for m, s in self._zset_store[key] if m not in members
            ]
            count = before - len(self._zset_store[key])
        return count

    async def zrangebyscore(
        self, key: str, min: float, max: float
    ) -> list[str]:
        if key not in self._zset_store:
            return []
        return [
            m for m, s in self._zset_store[key] if min <= s <= max
        ]

    async def zrevrangebyscore(
        self,
        key: str,
        max: float,
        min: float,
        start: int | None = None,
        num: int | None = None,
    ) -> list[str]:
        if key not in self._zset_store:
            return []
        results = [
            m for m, s in self._zset_store[key] if min <= s <= max
        ]
        results.reverse()
        if start is not None and num is not None:
            results = results[start : start + num]
        elif num is not None:
            results = results[:num]
        return results

    async def zrange(self, key: str, start: int, stop: int) -> list[str]:
        if key not in self._zset_store:
            return []
        items = self._zset_store[key]
        end = stop + 1 if stop >= 0 else len(items) + stop + 1
        return [m for m, _s in items[start:end]]

    async def zrevrange(self, key: str, start: int, stop: int) -> list[str]:
        if key not in self._zset_store:
            return []
        items = list(reversed(self._zset_store[key]))
        end = stop + 1 if stop >= 0 else len(items) + stop + 1
        return [m for m, _s in items[start:end]]

    async def zcard(self, key: str) -> int:
        return len(self._zset_store.get(key, []))

    async def zremrangebyrank(self, key: str, start: int, stop: int) -> int:
        if key not in self._zset_store:
            return 0
        before = len(self._zset_store[key])
        # Python slice: stop is inclusive in Redis
        end = stop + 1 if stop >= 0 else len(self._zset_store[key]) + stop + 1
        del self._zset_store[key][start:end]
        return before - len(self._zset_store[key])

    async def lpush(self, key: str, *values: str) -> int:
        if key not in self._list_store:
            self._list_store[key] = []
        for v in values:
            self._list_store[key].insert(0, v)
        return len(self._list_store[key])

    async def ltrim(self, key: str, start: int, stop: int) -> bool:
        if key in self._list_store:
            self._list_store[key] = self._list_store[key][start : stop + 1]
        return True

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        if key not in self._list_store:
            return []
        end = stop + 1 if stop >= 0 else None
        return self._list_store[key][start:end]

    async def expire(self, key: str, seconds: int) -> bool:
        self._expiry[key] = seconds
        return True

    async def publish(self, channel: str, message: str) -> int:
        return 1

    async def ping(self) -> bool:
        return True


# ── Fixtures ──


@pytest.fixture
def config() -> ProfitConfig:
    """기본 설정 fixture."""
    ConfigManager.reset()
    return ProfitConfig()


@pytest.fixture
def fake_redis() -> FakeRedis:
    """인메모리 Redis fixture."""
    return FakeRedis()


@pytest.fixture
def fake_llm() -> FakeLLMClient:
    """Fake LLM 클라이언트 fixture."""
    return FakeLLMClient()
