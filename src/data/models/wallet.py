"""지갑(잔고) 모델."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models.base import Base, UUIDPrimaryKeyMixin


class Wallet(UUIDPrimaryKeyMixin, Base):
    """거래소 잔고 테이블."""

    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("exchange_name", "asset", name="uq_wallet_exchange_asset"),
    )

    exchange_name: Mapped[str] = mapped_column(String(50), nullable=False)
    asset: Mapped[str] = mapped_column(String(20), nullable=False)

    # 잔고
    total_balance: Mapped[float] = mapped_column(Float, nullable=False)
    available_balance: Mapped[float] = mapped_column(Float, nullable=False)
    frozen_balance: Mapped[float] = mapped_column(Float, default=0)

    # 리스크 제어 (reserve_ratio)
    reserve_balance: Mapped[float] = mapped_column(Float, nullable=False)
    available_for_trading: Mapped[float | None] = mapped_column(Float)

    # 동기화
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
