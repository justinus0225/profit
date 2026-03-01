"""거래소 데이터 모델 (Pydantic).

ccxt 응답을 래핑하는 정규화된 데이터 모델.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


class Ticker(BaseModel):
    """현재가 정보."""

    symbol: str
    timestamp: datetime
    last: float
    bid: float | None = None
    ask: float | None = None
    bid_volume: float | None = None
    ask_volume: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    change: float | None = None
    percentage: float | None = None
    quote_volume: float | None = None


class OHLCV(BaseModel):
    """캔들 데이터."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class AssetBalance(BaseModel):
    """개별 자산 잔고."""

    asset: str
    total: float = 0.0
    available: float = 0.0
    frozen: float = 0.0


class ExchangeBalance(BaseModel):
    """거래소 전체 잔고."""

    exchange_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    balances: dict[str, AssetBalance] = Field(default_factory=dict)
    total_usdt: float | None = None


class ExchangeOrder(BaseModel):
    """거래소 주문 정보 (ccxt 응답 정규화)."""

    exchange_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None
    filled: float = 0.0
    remaining: float | None = None
    average: float | None = None
    status: str = ""  # open, closed, canceled
    fee: float = 0.0
    fee_currency: str | None = None
    timestamp: datetime | None = None
    client_order_id: str | None = None


class TradingPair(BaseModel):
    """거래 페어 정보."""

    symbol: str  # "BTC/USDT"
    base: str  # "BTC"
    quote: str  # "USDT"
    min_amount: float | None = None
    min_cost: float | None = None
    price_precision: int | None = None
    amount_precision: int | None = None
    active: bool = True
