"""매매 시그널 모델 - TimescaleDB Hypertable."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models.base import Base


class Signal(Base):
    """퀀트 에이전트 매매 시그널 (TimescaleDB hypertable).

    PK: (time, coin_id) 복합키.
    """

    __tablename__ = "signals"

    time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    coin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, nullable=False
    )

    # 시그널 정보
    signal_type: Mapped[str] = mapped_column(String(20), nullable=False)
    strength: Mapped[int] = mapped_column(Integer, nullable=False)

    # 전략별 기여도
    strategy_contributions: Mapped[dict | None] = mapped_column(JSONB)

    # 합의 상태
    quorum_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_manager_veto: Mapped[bool] = mapped_column(Boolean, default=False)

    # 메타
    analyst_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    quant_agent_id: Mapped[str | None] = mapped_column(String(50))
    timeframe: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
