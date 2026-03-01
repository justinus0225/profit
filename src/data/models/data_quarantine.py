"""이상치 격리 모델 (P10) - TimescaleDB Hypertable."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models.base import Base


class DataQuarantine(Base):
    """이상치 데이터 격리 테이블 (ARCHITECTURE.md P10).

    Z-Score / IQR 기반 이상치 탐지 결과를 격리 보관한다.
    PK: (time, coin_id, field_name) 복합키.
    """

    __tablename__ = "data_quarantine"

    time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    coin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, nullable=False
    )
    field_name: Mapped[str] = mapped_column(
        String(50), primary_key=True, nullable=False
    )

    # 원본 데이터
    raw_value: Mapped[float] = mapped_column(Float, nullable=False)

    # 이상치 탐지
    anomaly_method: Mapped[str] = mapped_column(String(50), nullable=False)
    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_exceeded: Mapped[float] = mapped_column(Float, nullable=False)

    # 힐링
    healing_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    healing_method: Mapped[str | None] = mapped_column(String(50))
    healed_value: Mapped[float | None] = mapped_column(Float)

    # 컨텍스트
    window_size: Mapped[int | None] = mapped_column(Integer)
    window_anomaly_ratio: Mapped[float | None] = mapped_column(Float)

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
