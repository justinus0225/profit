"""일일 감시 목록(Watchlist) 모델."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models.base import Base, UUIDPrimaryKeyMixin


class Watchlist(UUIDPrimaryKeyMixin, Base):
    """일일 코인 선별 결과 (TRADING_FLOW.md Section 3)."""

    __tablename__ = "watchlist"
    __table_args__ = (
        UniqueConstraint("selection_date", "coin_id", name="uq_watchlist_date_coin"),
    )

    selection_date: Mapped[date] = mapped_column(Date, nullable=False)
    coin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # 2단계 필터 결과
    stage1_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    stage1_score: Mapped[int | None] = mapped_column(Integer)
    stage2_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # 순위
    selection_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    total_selected: Mapped[int] = mapped_column(Integer, nullable=False)
    days_on_watchlist: Mapped[int] = mapped_column(Integer, default=0)

    # 선정 사유
    is_whitelist: Mapped[bool] = mapped_column(Boolean, default=False)
    is_token_unlock_warning: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
