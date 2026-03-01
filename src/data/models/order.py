"""주문 모델 (OMS 상태 머신 - P1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.data.models.base import Base, UUIDPrimaryKeyMixin


class OrderState(str, Enum):
    """주문 상태 머신 (ARCHITECTURE.md P1)."""

    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class Order(UUIDPrimaryKeyMixin, Base):
    """주문 테이블 - OMS 상태 머신 + 멱등성 키."""

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="orders_quantity_positive"),
        CheckConstraint("price > 0 OR price IS NULL", name="orders_price_positive"),
    )

    # 멱등성 (P1)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4
    )
    exchange_order_id: Mapped[str | None] = mapped_column(String(100))

    # 코인 참조
    coin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # 상태 머신
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=OrderState.CREATED.value
    )

    # 주문 파라미터
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float | None] = mapped_column(Float)

    # 부분 체결 추적
    quantity_filled: Mapped[float] = mapped_column(Float, default=0)
    quantity_remaining: Mapped[float | None] = mapped_column(Float)
    average_fill_price: Mapped[float | None] = mapped_column(Float)

    # 실행 에이전트
    execution_agent_id: Mapped[str | None] = mapped_column(String(50))

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_update: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # 에러 및 조정
    error_message: Mapped[str | None] = mapped_column(Text)
    last_reconciliation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciliation_status: Mapped[str | None] = mapped_column(String(50))

    # 관계
    coin: Mapped["Coin"] = relationship(back_populates="orders")  # noqa: F821
    trades: Mapped[list["Trade"]] = relationship(back_populates="order")  # noqa: F821
