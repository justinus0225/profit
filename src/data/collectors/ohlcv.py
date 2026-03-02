"""OHLCV 데이터 수집기.

거래소에서 주기적으로 OHLCV 캔들 데이터를 수집하여
Redis 이벤트로 발행한다 (DataEngineerAgent가 품질 검증 후 DB 저장).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from src.exchange.client import ExchangeClient

logger = logging.getLogger(__name__)


class OHLCVCollector:
    """OHLCV 캔들 주기적 수집기."""

    def __init__(
        self,
        exchange_client: ExchangeClient,
        redis_client: aioredis.Redis,
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
        interval_seconds: int = 60,
    ) -> None:
        self._exchange = exchange_client
        self._redis = redis_client
        self._symbols = symbols or []
        self._timeframes = timeframes or ["1h"]
        self._interval = interval_seconds
        self._running = False

    async def start(self) -> None:
        """수집 루프를 시작한다."""
        self._running = True
        logger.info(
            "OHLCV collector started: %d symbols, timeframes=%s",
            len(self._symbols), self._timeframes,
        )
        while self._running:
            await self._collect_all()
            await asyncio.sleep(self._interval)

    def stop(self) -> None:
        self._running = False

    def update_symbols(self, symbols: list[str]) -> None:
        """수집 대상 심볼을 업데이트한다."""
        self._symbols = symbols
        logger.info("OHLCV collector symbols updated: %d", len(symbols))

    async def _collect_all(self) -> None:
        """전체 심볼/타임프레임 조합의 OHLCV를 수집한다."""
        for symbol in self._symbols:
            for tf in self._timeframes:
                try:
                    candles = await self._exchange.fetch_ohlcv(
                        symbol, tf, limit=100, agent_name="collector",
                    )
                    if candles:
                        payload: dict[str, Any] = {
                            "symbol": symbol,
                            "timeframe": tf,
                            "candle_count": len(candles),
                            "latest_close": candles[-1].close,
                            "latest_volume": candles[-1].volume,
                            "collected_at": datetime.now(tz=timezone.utc).isoformat(),
                        }
                        await self._redis.publish(
                            "data:ohlcv_received",
                            __import__("json").dumps(payload),
                        )
                except Exception:
                    logger.warning(
                        "OHLCV collect failed: %s/%s", symbol, tf, exc_info=True,
                    )

    async def backfill(
        self, symbol: str, timeframe: str, limit: int = 500
    ) -> int:
        """과거 OHLCV 데이터를 일괄 수집한다."""
        try:
            candles = await self._exchange.fetch_ohlcv(
                symbol, timeframe, limit=limit, agent_name="collector",
            )
            logger.info("Backfill %s/%s: %d candles", symbol, timeframe, len(candles))
            return len(candles)
        except Exception:
            logger.warning("Backfill failed: %s/%s", symbol, timeframe, exc_info=True)
            return 0
