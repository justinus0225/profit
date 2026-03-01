"""ccxt 기반 거래소 클라이언트.

ARCHITECTURE.md: 거래소 연동 계층
- 현물(Spot) 거래 전용
- ccxt 비동기 클라이언트 래핑
- 멱등성 키(P1) 기반 주문 전송
- Rate Limiter 경유 API 호출
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

import ccxt.async_support as ccxt_async

from src.core.config import ExchangeConfig, ExecutionConfig
from src.exchange.models import (
    AssetBalance,
    ExchangeBalance,
    ExchangeOrder,
    OHLCV,
    OrderSide,
    OrderType,
    Ticker,
    TradingPair,
)
from src.exchange.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class ExchangeError(Exception):
    """거래소 API 호출 실패."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        self.retryable = retryable
        super().__init__(message)


class ExchangeClient:
    """ccxt 기반 현물 거래소 클라이언트.

    모든 API 호출은 RateLimiter를 경유하며,
    주문 전송 시 멱등성 키(clientOrderId)를 자동 생성한다.
    """

    def __init__(
        self,
        exchange_config: ExchangeConfig,
        execution_config: ExecutionConfig,
        rate_limiter: RateLimiter,
        exchange_id: str = "binance",
        paper_trading: bool = False,
    ) -> None:
        self._exchange_config = exchange_config
        self._execution_config = execution_config
        self._rate_limiter = rate_limiter
        self._exchange_id = exchange_id
        self._paper_trading = paper_trading
        self._exchange: ccxt_async.Exchange | None = None

    async def initialize(self) -> None:
        """거래소 연결을 초기화한다."""
        exchange_class = getattr(ccxt_async, self._exchange_id, None)
        if exchange_class is None:
            raise ExchangeError(f"Unsupported exchange: {self._exchange_id}", retryable=False)

        config: dict = {
            "apiKey": os.getenv("EXCHANGE_API_KEY", ""),
            "secret": os.getenv("EXCHANGE_API_SECRET", ""),
            "enableRateLimit": False,  # 자체 Rate Limiter 사용
            "options": {
                "defaultType": "spot",
            },
        }

        if self._paper_trading:
            config["sandbox"] = True

        self._exchange = exchange_class(config)
        await self._exchange.load_markets()
        logger.info(
            "Exchange initialized: %s (paper=%s, markets=%d)",
            self._exchange_id,
            self._paper_trading,
            len(self._exchange.markets),
        )

    async def close(self) -> None:
        """거래소 연결을 종료한다."""
        if self._exchange:
            await self._exchange.close()

    def _ensure_exchange(self) -> ccxt_async.Exchange:
        if self._exchange is None:
            raise ExchangeError("Exchange not initialized", retryable=False)
        return self._exchange

    # ── 시세 조회 ──

    async def fetch_ticker(self, symbol: str, agent_name: str = "quant") -> Ticker:
        """현재가를 조회한다."""
        exchange = self._ensure_exchange()
        if not await self._rate_limiter.acquire(agent_name, weight=1):
            raise ExchangeError("Rate limit exceeded for ticker fetch")

        try:
            raw = await exchange.fetch_ticker(symbol)
            return Ticker(
                symbol=raw["symbol"],
                timestamp=datetime.fromtimestamp(raw["timestamp"] / 1000, tz=timezone.utc),
                last=raw.get("last", 0),
                bid=raw.get("bid"),
                ask=raw.get("ask"),
                bid_volume=raw.get("bidVolume"),
                ask_volume=raw.get("askVolume"),
                open=raw.get("open"),
                high=raw.get("high"),
                low=raw.get("low"),
                close=raw.get("close"),
                volume=raw.get("baseVolume"),
                change=raw.get("change"),
                percentage=raw.get("percentage"),
                quote_volume=raw.get("quoteVolume"),
            )
        except ccxt_async.BaseError as e:
            raise ExchangeError(str(e)) from e

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
        agent_name: str = "data_engineer",
    ) -> list[OHLCV]:
        """OHLCV 캔들 데이터를 조회한다."""
        exchange = self._ensure_exchange()
        if not await self._rate_limiter.acquire(agent_name, weight=2):
            raise ExchangeError("Rate limit exceeded for OHLCV fetch")

        try:
            raw_list = await exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=limit, since=since
            )
            return [
                OHLCV(
                    timestamp=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                    open=row[1],
                    high=row[2],
                    low=row[3],
                    close=row[4],
                    volume=row[5],
                )
                for row in raw_list
            ]
        except ccxt_async.BaseError as e:
            raise ExchangeError(str(e)) from e

    # ── 잔고 조회 ──

    async def fetch_balance(self, agent_name: str = "executor") -> ExchangeBalance:
        """거래소 잔고를 조회한다."""
        exchange = self._ensure_exchange()
        if not await self._rate_limiter.acquire(agent_name, weight=5):
            raise ExchangeError("Rate limit exceeded for balance fetch")

        try:
            raw = await exchange.fetch_balance()
            balances: dict[str, AssetBalance] = {}
            for asset, info in raw.get("total", {}).items():
                if info and info > 0:
                    balances[asset] = AssetBalance(
                        asset=asset,
                        total=info,
                        available=raw.get("free", {}).get(asset, 0) or 0,
                        frozen=raw.get("used", {}).get(asset, 0) or 0,
                    )

            total_usdt = balances.get("USDT", AssetBalance(asset="USDT")).total
            return ExchangeBalance(
                exchange_name=self._exchange_id,
                balances=balances,
                total_usdt=total_usdt,
            )
        except ccxt_async.BaseError as e:
            raise ExchangeError(str(e)) from e

    # ── 주문 실행 ──

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
        idempotency_key: uuid.UUID | None = None,
        agent_name: str = "executor",
    ) -> ExchangeOrder:
        """주문을 전송한다.

        멱등성 키를 clientOrderId로 전달하여 중복 주문을 방지한다 (P1).
        """
        exchange = self._ensure_exchange()

        # Rate Limiter: 가중치 + 주문 슬롯
        if not await self._rate_limiter.acquire(agent_name, weight=1):
            raise ExchangeError("Rate limit exceeded for order creation")
        if not await self._rate_limiter.acquire_order_slot():
            raise ExchangeError("Order rate limit exceeded (max orders/sec)")

        client_order_id = str(idempotency_key or uuid.uuid4())
        params: dict = {"newClientOrderId": client_order_id}

        try:
            raw = await exchange.create_order(
                symbol=symbol,
                type=order_type.value,
                side=side.value,
                amount=quantity,
                price=price,
                params=params,
            )

            return self._parse_order(raw, client_order_id)
        except ccxt_async.InsufficientFunds as e:
            raise ExchangeError(f"Insufficient funds: {e}", retryable=False) from e
        except ccxt_async.InvalidOrder as e:
            raise ExchangeError(f"Invalid order: {e}", retryable=False) from e
        except ccxt_async.BaseError as e:
            raise ExchangeError(str(e)) from e

    async def cancel_order(
        self,
        exchange_order_id: str,
        symbol: str,
        agent_name: str = "executor",
    ) -> ExchangeOrder:
        """주문을 취소한다."""
        exchange = self._ensure_exchange()
        if not await self._rate_limiter.acquire(agent_name, weight=1):
            raise ExchangeError("Rate limit exceeded for order cancel")

        try:
            raw = await exchange.cancel_order(exchange_order_id, symbol)
            return self._parse_order(raw)
        except ccxt_async.OrderNotFound as e:
            raise ExchangeError(f"Order not found: {e}", retryable=False) from e
        except ccxt_async.BaseError as e:
            raise ExchangeError(str(e)) from e

    async def fetch_order(
        self,
        exchange_order_id: str,
        symbol: str,
        agent_name: str = "oms",
    ) -> ExchangeOrder:
        """주문 상태를 조회한다 (OMS 조정용)."""
        exchange = self._ensure_exchange()
        if not await self._rate_limiter.acquire(agent_name, weight=1):
            raise ExchangeError("Rate limit exceeded for order fetch")

        try:
            raw = await exchange.fetch_order(exchange_order_id, symbol)
            return self._parse_order(raw)
        except ccxt_async.OrderNotFound as e:
            raise ExchangeError(f"Order not found: {e}", retryable=False) from e
        except ccxt_async.BaseError as e:
            raise ExchangeError(str(e)) from e

    async def fetch_open_orders(
        self,
        symbol: str | None = None,
        agent_name: str = "oms",
    ) -> list[ExchangeOrder]:
        """미체결 주문 목록을 조회한다."""
        exchange = self._ensure_exchange()
        if not await self._rate_limiter.acquire(agent_name, weight=5):
            raise ExchangeError("Rate limit exceeded for open orders fetch")

        try:
            raw_list = await exchange.fetch_open_orders(symbol)
            return [self._parse_order(raw) for raw in raw_list]
        except ccxt_async.BaseError as e:
            raise ExchangeError(str(e)) from e

    # ── 거래 페어 정보 ──

    async def get_trading_pair(self, symbol: str) -> TradingPair | None:
        """거래 페어 정보를 반환한다."""
        exchange = self._ensure_exchange()
        market = exchange.market(symbol) if symbol in exchange.markets else None
        if not market:
            return None

        limits = market.get("limits", {})
        precision = market.get("precision", {})

        return TradingPair(
            symbol=market["symbol"],
            base=market["base"],
            quote=market["quote"],
            min_amount=limits.get("amount", {}).get("min"),
            min_cost=limits.get("cost", {}).get("min"),
            price_precision=precision.get("price"),
            amount_precision=precision.get("amount"),
            active=market.get("active", True),
        )

    # ── 내부 유틸 ──

    def _parse_order(
        self,
        raw: dict,
        client_order_id: str | None = None,
    ) -> ExchangeOrder:
        """ccxt 주문 응답을 ExchangeOrder로 변환한다."""
        fee_info = raw.get("fee") or {}
        ts = raw.get("timestamp")
        return ExchangeOrder(
            exchange_order_id=str(raw.get("id", "")),
            symbol=raw.get("symbol", ""),
            side=OrderSide(raw.get("side", "buy")),
            order_type=OrderType(raw.get("type", "limit")),
            quantity=raw.get("amount", 0),
            price=raw.get("price"),
            filled=raw.get("filled", 0),
            remaining=raw.get("remaining"),
            average=raw.get("average"),
            status=raw.get("status", ""),
            fee=fee_info.get("cost", 0) or 0,
            fee_currency=fee_info.get("currency"),
            timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None,
            client_order_id=client_order_id or raw.get("clientOrderId"),
        )
