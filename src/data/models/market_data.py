"""시장 데이터 (OHLCV 캔들) 모델 - TimescaleDB Hypertable."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models.base import Base


class MarketData(Base):
    """OHLCV 캔들 데이터 (TimescaleDB hypertable).

    시간축 파티셔닝과 자동 압축이 적용된다.
    PK: (time, coin_id, timeframe) 복합키.
    """

    __tablename__ = "market_data"

    time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    coin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, nullable=False
    )
    timeframe: Mapped[str] = mapped_column(
        String(10), primary_key=True, nullable=False
    )

    # OHLCV
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    volume_usd: Mapped[float | None] = mapped_column(Float)

    # 데이터 품질 (P10)
    healing_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    healing_method: Mapped[str | None] = mapped_column(String(50))
    quarantine_reason: Mapped[str | None] = mapped_column(Text)

    # 메타
    quote_asset: Mapped[str] = mapped_column(String(20), default="USDT")
    exchange_name: Mapped[str] = mapped_column(String(50), default="binance")
    source_type: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
