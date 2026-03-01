"""포지션(보유 현황) 모델."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.data.models.base import Base, UUIDPrimaryKeyMixin


class Position(UUIDPrimaryKeyMixin, Base):
    """활성 포지션 테이블."""

    __tablename__ = "positions"

    # 코인 참조
    coin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # 진입 정보
    entry_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)

    # 보유 기간 관리
    holding_type: Mapped[str] = mapped_column(String(20), nullable=False)
    max_holding_days: Mapped[int | None] = mapped_column()
    target_close_date: Mapped[date | None] = mapped_column(Date)

    # 출구 전략
    target_price: Mapped[float | None] = mapped_column(Float)
    stop_loss_price: Mapped[float | None] = mapped_column(Float)
    trailing_stop_pct: Mapped[float | None] = mapped_column(Float)

    # 현재 상태
    current_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    current_price_usd: Mapped[float | None] = mapped_column(Float)
    unrealized_pnl_usd: Mapped[float | None] = mapped_column(Float)
    unrealized_pnl_pct: Mapped[float | None] = mapped_column(Float)

    # 전략 정보
    entry_strategy: Mapped[str | None] = mapped_column(String(100))
    entry_signal_score: Mapped[float | None] = mapped_column(Float)
    entry_fees_usd: Mapped[float] = mapped_column(Float, default=0)

    # 상태
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    is_stop_loss_active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_trailing_stop_active: Mapped[bool] = mapped_column(Boolean, default=False)

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # 관계
    coin: Mapped["Coin"] = relationship(back_populates="positions")  # noqa: F821
    trades: Mapped[list["Trade"]] = relationship(back_populates="position")  # noqa: F821
