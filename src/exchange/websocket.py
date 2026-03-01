"""WebSocket 실시간 가격 스트림 관리.

ARCHITECTURE.md: 실시간 데이터 스트림
- 감시 목록 코인의 실시간 가격 수신
- 가격 급변(price spike) 이벤트 감지
- Redis pub/sub로 에이전트에게 브로드캐스트
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

import ccxt.pro as ccxt_pro

from src.exchange.models import Ticker

logger = logging.getLogger(__name__)


class PriceSpikeEvent:
    """가격 급변 이벤트."""

    def __init__(
        self,
        symbol: str,
        price: float,
        change_pct: float,
        window_minutes: int,
        timestamp: datetime,
    ) -> None:
        self.symbol = symbol
        self.price = price
        self.change_pct = change_pct
        self.window_minutes = window_minutes
        self.timestamp = timestamp

    def to_dict(self) -> dict:
        return {
            "event": "price_spike",
            "symbol": self.symbol,
            "price": self.price,
            "change_pct": self.change_pct,
            "window_minutes": self.window_minutes,
            "timestamp": self.timestamp.isoformat(),
        }


class WebSocketManager:
    """거래소 WebSocket 스트림 관리.

    ccxt.pro를 사용하여 실시간 가격 데이터를 수신하고,
    가격 급변 시 이벤트를 발생시킨다.
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        price_spike_threshold: float = 0.03,
        price_spike_window_minutes: int = 5,
        paper_trading: bool = False,
    ) -> None:
        self._exchange_id = exchange_id
        self._spike_threshold = price_spike_threshold
        self._spike_window = price_spike_window_minutes
        self._paper_trading = paper_trading
        self._exchange: ccxt_pro.Exchange | None = None
        self._running = False
        self._symbols: list[str] = []
        self._tasks: list[asyncio.Task] = []

        # 가격 이력 (spike 감지용): symbol → [(timestamp, price), ...]
        self._price_history: dict[str, list[tuple[float, float]]] = {}

        # 콜백
        self._on_ticker: Callable[[Ticker], Coroutine[Any, Any, None]] | None = None
        self._on_spike: Callable[[PriceSpikeEvent], Coroutine[Any, Any, None]] | None = None

    async def initialize(self) -> None:
        """WebSocket 연결을 초기화한다."""
        import os

        exchange_class = getattr(ccxt_pro, self._exchange_id, None)
        if exchange_class is None:
            raise RuntimeError(f"ccxt.pro does not support: {self._exchange_id}")

        config: dict = {
            "apiKey": os.getenv("EXCHANGE_API_KEY", ""),
            "secret": os.getenv("EXCHANGE_API_SECRET", ""),
            "enableRateLimit": False,
            "options": {"defaultType": "spot"},
        }
        if self._paper_trading:
            config["sandbox"] = True

        self._exchange = ccxt_pro.binance(config) if self._exchange_id == "binance" else exchange_class(config)
        logger.info("WebSocket initialized: %s", self._exchange_id)

    def on_ticker(
        self, callback: Callable[[Ticker], Coroutine[Any, Any, None]]
    ) -> None:
        """틱 데이터 수신 콜백을 등록한다."""
        self._on_ticker = callback

    def on_price_spike(
        self, callback: Callable[[PriceSpikeEvent], Coroutine[Any, Any, None]]
    ) -> None:
        """가격 급변 이벤트 콜백을 등록한다."""
        self._on_spike = callback

    async def subscribe(self, symbols: list[str]) -> None:
        """심볼 목록의 실시간 가격을 구독한다."""
        self._symbols = symbols
        self._running = True

        for symbol in symbols:
            task = asyncio.create_task(self._watch_ticker(symbol))
            self._tasks.append(task)

        logger.info("WebSocket subscribed: %d symbols", len(symbols))

    async def unsubscribe(self) -> None:
        """모든 구독을 해제한다."""
        self._running = False
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        self._symbols.clear()
        logger.info("WebSocket unsubscribed all")

    async def close(self) -> None:
        """WebSocket 연결을 종료한다."""
        await self.unsubscribe()
        if self._exchange:
            await self._exchange.close()

    async def _watch_ticker(self, symbol: str) -> None:
        """단일 심볼의 틱 데이터를 지속 수신한다."""
        if not self._exchange:
            return

        while self._running:
            try:
                raw = await self._exchange.watch_ticker(symbol)
                ticker = self._parse_ticker(raw)

                # 콜백 호출
                if self._on_ticker:
                    await self._on_ticker(ticker)

                # 가격 급변 감지
                await self._check_price_spike(symbol, ticker)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("WebSocket error for %s", symbol)
                await asyncio.sleep(5)

    def _parse_ticker(self, raw: dict) -> Ticker:
        ts = raw.get("timestamp")
        return Ticker(
            symbol=raw.get("symbol", ""),
            timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else datetime.now(tz=timezone.utc),
            last=raw.get("last", 0),
            bid=raw.get("bid"),
            ask=raw.get("ask"),
            volume=raw.get("baseVolume"),
            percentage=raw.get("percentage"),
            quote_volume=raw.get("quoteVolume"),
        )

    async def _check_price_spike(self, symbol: str, ticker: Ticker) -> None:
        """가격 급변을 감지한다 (config event.price_spike)."""
        now = time.time()
        price = ticker.last
        if price <= 0:
            return

        # 이력에 추가
        if symbol not in self._price_history:
            self._price_history[symbol] = []
        history = self._price_history[symbol]
        history.append((now, price))

        # 윈도우 밖 데이터 제거
        cutoff = now - self._spike_window * 60
        self._price_history[symbol] = [(t, p) for t, p in history if t >= cutoff]
        history = self._price_history[symbol]

        if len(history) < 2:
            return

        # 윈도우 내 최초 가격과 비교
        first_price = history[0][1]
        change_pct = (price - first_price) / first_price

        if abs(change_pct) >= self._spike_threshold and self._on_spike:
            event = PriceSpikeEvent(
                symbol=symbol,
                price=price,
                change_pct=change_pct,
                window_minutes=self._spike_window,
                timestamp=ticker.timestamp,
            )
            await self._on_spike(event)
            # 동일 방향 연속 이벤트 방지: 이력 초기화
            self._price_history[symbol] = [(now, price)]

    @property
    def subscribed_symbols(self) -> list[str]:
        return list(self._symbols)

    @property
    def is_running(self) -> bool:
        return self._running
