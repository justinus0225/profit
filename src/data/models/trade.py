"""체결 내역 모델 - TimescaleDB Hypertable."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.data.models.base import Base, UUIDPrimaryKeyMixin


class Trade(UUIDPrimaryKeyMixin, Base):
    """매매 체결 내역 (TimescaleDB hypertable)."""

    __tablename__ = "trades"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # 참조
    coin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # 매매 유형
    trade_type: Mapped[str] = mapped_column(String(20), nullable=False)
    order_side: Mapped[str] = mapped_column(String(10), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # 체결 정보
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    total_usd: Mapped[float] = mapped_column(Float, nullable=False)

    # 슬리피지 및 수수료
    expected_price: Mapped[float | None] = mapped_column(Float)
    slippage_pct: Mapped[float | None] = mapped_column(Float)
    fee_usd: Mapped[float] = mapped_column(Float, default=0)

    # 컨텍스트
    execution_agent_id: Mapped[str | None] = mapped_column(String(50))
    strategy_name: Mapped[str | None] = mapped_column(String(100))
    signal_score: Mapped[float | None] = mapped_column(Float)
    exchange_name: Mapped[str] = mapped_column(String(50), default="binance")

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # 관계
    position: Mapped["Position | None"] = relationship(back_populates="trades")  # noqa: F821
    order: Mapped["Order | None"] = relationship(back_populates="trades")  # noqa: F821
