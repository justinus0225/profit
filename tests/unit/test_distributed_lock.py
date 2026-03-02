"""분산 락 단위 테스트 (P9)."""

from __future__ import annotations

import pytest

from src.core.config import ConcurrencyConfig
from src.core.distributed_lock import (
    DistributedLock,
    balance_lock,
    order_lock,
    position_lock,
)


@pytest.fixture
def concurrency_config() -> ConcurrencyConfig:
    return ConcurrencyConfig()


class TestDistributedLock:
    @pytest.mark.asyncio
    async def test_acquire_and_release(
        self, fake_redis, concurrency_config
    ) -> None:
        lock = DistributedLock(fake_redis, "test:lock", concurrency_config)
        acquired = await lock.acquire()
        assert acquired is True
        assert lock.is_locked is True

        released = await lock.release()
        assert released is True
        assert lock.is_locked is False

    @pytest.mark.asyncio
    async def test_acquire_twice_fails(
        self, fake_redis, concurrency_config
    ) -> None:
        lock1 = DistributedLock(fake_redis, "shared:lock", concurrency_config)
        lock2 = DistributedLock(fake_redis, "shared:lock", concurrency_config)

        assert await lock1.acquire() is True
        # 같은 키에 대해 NX이므로 실패해야 함
        assert await lock2.acquire() is False

    @pytest.mark.asyncio
    async def test_context_manager(
        self, fake_redis, concurrency_config
    ) -> None:
        lock = DistributedLock(fake_redis, "ctx:lock", concurrency_config)
        async with lock as acquired:
            assert acquired is True
            assert lock.is_locked is True
        assert lock.is_locked is False


class TestFactoryFunctions:
    def test_balance_lock(self, fake_redis, concurrency_config) -> None:
        lock = balance_lock(fake_redis, concurrency_config)
        assert "balance" in lock._key

    def test_position_lock(self, fake_redis, concurrency_config) -> None:
        lock = position_lock(fake_redis, "BTC/KRW", concurrency_config)
        assert "position" in lock._key
        assert "BTC" in lock._key

    def test_order_lock(self, fake_redis, concurrency_config) -> None:
        lock = order_lock(fake_redis, "ETH/KRW", concurrency_config)
        assert "order" in lock._key
        assert "ETH" in lock._key
