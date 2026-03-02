"""Redis 기반 분산 락 (Distributed Lock).

ARCHITECTURE.md P9: 경쟁 상태 방지.
- Redlock 알고리즘 (단일 Redis 인스턴스)
- SET NX PX 원자적 획득
- TTL 자동 해제 (데드락 방지)
- 컨텍스트 매니저 지원

공유 자원별 락 전략:
- lock:balance         → 전역, TTL 10초 (잔고 조회→차감→주문 원자적 수행)
- lock:position:{sym}  → 심볼별, TTL 5초 (동일 코인 동시 주문 방지)
- lock:order:{sym}     → 심볼별, TTL 5초 (동일 코인 중복 주문 방지)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from types import TracebackType

import redis.asyncio as aioredis

from src.core.config import ConcurrencyConfig

logger = logging.getLogger(__name__)


class DistributedLock:
    """Redis 기반 분산 락.

    async with DistributedLock(redis, "lock:balance", config) as acquired:
        if acquired:
            ...  # 임계 섹션
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        key: str,
        config: ConcurrencyConfig,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        self._redis = redis_client
        self._key = key
        self._config = config
        self._ttl = ttl_seconds or self._default_ttl(key)
        self._token = str(uuid.uuid4())
        self._acquired = False

    def _default_ttl(self, key: str) -> int:
        """락 키에 따른 기본 TTL을 반환한다."""
        if key == "lock:balance":
            return self._config.balance_lock_ttl_seconds
        # position, order 등 심볼별 락
        return self._config.order_lock_ttl_seconds

    async def acquire(self) -> bool:
        """락 획득을 시도한다. 실패 시 설정된 횟수만큼 재시도한다."""
        retry_attempts = self._config.lock_retry_attempts
        retry_delay_s = self._config.lock_retry_delay_ms / 1000.0

        for attempt in range(retry_attempts):
            result = await self._redis.set(
                self._key,
                self._token,
                nx=True,
                px=self._ttl * 1000,  # 밀리초 단위
            )
            if result:
                self._acquired = True
                logger.debug(
                    "Lock acquired: key=%s, token=%s, ttl=%ds",
                    self._key,
                    self._token[:8],
                    self._ttl,
                )
                return True

            if attempt < retry_attempts - 1:
                await asyncio.sleep(retry_delay_s)

        logger.warning(
            "Lock acquisition failed after %d attempts: key=%s",
            retry_attempts,
            self._key,
        )
        return False

    async def release(self) -> bool:
        """락을 해제한다. 자신이 소유한 락만 해제 가능하다."""
        if not self._acquired:
            return False

        # Lua 스크립트로 원자적 비교-삭제
        result = await self._redis.eval(  # type: ignore[union-attr]
            _RELEASE_SCRIPT,
            1,
            self._key,
            self._token,
        )
        self._acquired = False
        if result == 1:
            logger.debug("Lock released: key=%s", self._key)
            return True
        logger.warning("Lock release failed (token mismatch): key=%s", self._key)
        return False

    async def extend(self, additional_seconds: int) -> bool:
        """락의 TTL을 연장한다. 장시간 임계 섹션에 사용."""
        if not self._acquired:
            return False
        result = await self._redis.eval(  # type: ignore[union-attr]
            _EXTEND_SCRIPT,
            1,
            self._key,
            self._token,
            str(additional_seconds * 1000),
        )
        if result == 1:
            logger.debug("Lock extended: key=%s, +%ds", self._key, additional_seconds)
            return True
        return False

    @property
    def is_locked(self) -> bool:
        return self._acquired

    # ── 컨텍스트 매니저 ──

    async def __aenter__(self) -> bool:
        return await self.acquire()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.release()


# ── 팩토리 함수 ──


def balance_lock(
    redis_client: aioredis.Redis,
    config: ConcurrencyConfig,
) -> DistributedLock:
    """전역 잔고 락 (잔고 조회→차감→주문 원자적 수행)."""
    return DistributedLock(redis_client, "lock:balance", config)


def position_lock(
    redis_client: aioredis.Redis,
    symbol: str,
    config: ConcurrencyConfig,
) -> DistributedLock:
    """심볼별 포지션 락 (동일 코인 동시 조정 방지)."""
    return DistributedLock(redis_client, f"lock:position:{symbol}", config)


def order_lock(
    redis_client: aioredis.Redis,
    symbol: str,
    config: ConcurrencyConfig,
) -> DistributedLock:
    """심볼별 주문 락 (동일 코인 중복 주문 방지)."""
    return DistributedLock(redis_client, f"lock:order:{symbol}", config)


# ── Lua 스크립트 ──

# 비교-삭제: 토큰 일치 시에만 키 삭제
_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""

# 비교-연장: 토큰 일치 시에만 TTL 연장
_EXTEND_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
else
    return 0
end
"""
