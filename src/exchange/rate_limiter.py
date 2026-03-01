"""Token Bucket Rate Limiter (Redis 기반).

ARCHITECTURE.md: 거래소 API Rate Limiting
- Token Bucket 알고리즘으로 분당 최대 가중치 제어
- 에이전트별 우선순위 기반 토큰 배분
- 429 에러 시 지수 백오프 재시도
- 토큰 부족 시 backpressure 대기
"""

from __future__ import annotations

import asyncio
import logging
import time

import redis.asyncio as aioredis

from src.core.config import RateLimitConfig

logger = logging.getLogger(__name__)

# Redis Lua 스크립트: 원자적 토큰 소비
_CONSUME_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local weight = tonumber(ARGV[2])
local max_tokens = tonumber(ARGV[3])
local refill_rate = tonumber(ARGV[4])

-- 현재 토큰과 마지막 리필 시간 조회
local tokens = tonumber(redis.call('HGET', key, 'tokens') or max_tokens)
local last_refill = tonumber(redis.call('HGET', key, 'last_refill') or now)

-- 경과 시간 기반 토큰 리필
local elapsed = now - last_refill
local refill = elapsed * refill_rate
tokens = math.min(max_tokens, tokens + refill)

-- 토큰 소비 시도
if tokens >= weight then
    tokens = tokens - weight
    redis.call('HSET', key, 'tokens', tokens)
    redis.call('HSET', key, 'last_refill', now)
    redis.call('EXPIRE', key, 120)
    return 1
else
    redis.call('HSET', key, 'tokens', tokens)
    redis.call('HSET', key, 'last_refill', now)
    redis.call('EXPIRE', key, 120)
    return 0
end
"""


class RateLimiter:
    """Redis 기반 Token Bucket Rate Limiter.

    모든 에이전트가 공유하는 중앙 집중 API 호출 제어.
    에이전트 우선순위에 따라 토큰 부족 시 대기 순서가 결정된다.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        config: RateLimitConfig,
    ) -> None:
        self._redis = redis_client
        self._config = config
        self._script_sha: str | None = None
        self._key_weight = "profit:ratelimit:weight"
        self._key_orders = "profit:ratelimit:orders"

    async def _ensure_script(self) -> str:
        """Lua 스크립트를 Redis에 로드한다."""
        if self._script_sha is None:
            self._script_sha = await self._redis.script_load(_CONSUME_SCRIPT)
        return self._script_sha

    async def acquire(
        self,
        agent_name: str,
        weight: int = 1,
    ) -> bool:
        """API 호출 토큰을 획득한다.

        토큰 부족 시 에이전트 우선순위에 따라 대기한다.
        backpressure_wait_max_seconds 초과 시 False를 반환한다.
        """
        if not self._config.enabled:
            return True

        priority = self._get_priority(agent_name)
        max_wait = self._config.backpressure_wait_max_seconds
        # 낮은 우선순위 에이전트는 더 짧게 대기
        adjusted_wait = max_wait * (priority / 10.0)

        sha = await self._ensure_script()
        start = time.monotonic()

        while True:
            now = time.time()
            # 분당 최대 가중치 → 초당 리필 속도
            refill_rate = self._config.max_weight_per_minute / 60.0

            result = await self._redis.evalsha(
                sha,
                1,
                self._key_weight,
                str(now),
                str(weight),
                str(self._config.max_weight_per_minute),
                str(refill_rate),
            )

            if result == 1:
                return True

            elapsed = time.monotonic() - start
            if elapsed >= adjusted_wait:
                logger.warning(
                    "Rate limit: agent=%s exhausted wait (%.1fs, priority=%d)",
                    agent_name,
                    elapsed,
                    priority,
                )
                return False

            # 우선순위가 높을수록 짧은 간격으로 재시도
            sleep_time = max(0.05, 0.5 / priority)
            await asyncio.sleep(sleep_time)

    async def acquire_order_slot(self) -> bool:
        """초당 주문 전송 슬롯을 획득한다."""
        if not self._config.enabled:
            return True

        key = self._key_orders
        now = time.time()
        window_start = now - 1.0  # 1초 윈도우

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, 5)
        results = await pipe.execute()

        current_count = results[1]
        if current_count >= self._config.max_orders_per_second:
            # 추가한 멤버 제거
            await self._redis.zrem(key, str(now))
            return False
        return True

    def _get_priority(self, agent_name: str) -> int:
        """에이전트 우선순위를 반환한다 (높을수록 우선)."""
        priorities = self._config.agent_priority
        if agent_name == "executor":
            return priorities.executor
        if agent_name == "oms":
            return priorities.oms
        if agent_name == "quant":
            return priorities.quant
        if agent_name == "data_engineer":
            return priorities.data_engineer
        return 3  # 기본 우선순위

    async def get_status(self) -> dict:
        """현재 Rate Limiter 상태를 반환한다."""
        tokens = await self._redis.hget(self._key_weight, "tokens")
        order_count = await self._redis.zcard(self._key_orders)
        return {
            "enabled": self._config.enabled,
            "tokens_remaining": float(tokens) if tokens else self._config.max_weight_per_minute,
            "max_weight_per_minute": self._config.max_weight_per_minute,
            "orders_in_last_second": order_count or 0,
            "max_orders_per_second": self._config.max_orders_per_second,
        }
