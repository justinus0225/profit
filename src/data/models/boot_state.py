"""부트 시퀀스 상태 모델 (P12)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models.base import Base, UUIDPrimaryKeyMixin


class BootState(UUIDPrimaryKeyMixin, Base):
    """6단계 부트 시퀀스 체크포인트 (ARCHITECTURE.md P12, TRADING_FLOW.md 9.1)."""

    __tablename__ = "boot_state"

    boot_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False
    )
    boot_start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    boot_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    boot_status: Mapped[str | None] = mapped_column(String(50))

    # Phase 0: 인프라 점검
    phase_0_infra_check: Mapped[bool] = mapped_column(Boolean, default=False)
    phase_0_check_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Phase 1: 데이터 복구/백필
    phase_1_data_recovery: Mapped[bool] = mapped_column(Boolean, default=False)
    phase_1_backfill_count: Mapped[int | None] = mapped_column(Integer)

    # Phase 2: 지표 워밍업
    phase_2_indicator_warmup: Mapped[bool] = mapped_column(Boolean, default=False)
    phase_2_warmup_data: Mapped[dict | None] = mapped_column(JSONB)

    # Phase 3: OMS 동기화
    phase_3_oms_sync: Mapped[bool] = mapped_column(Boolean, default=False)
    phase_3_unexecuted_orders_count: Mapped[int | None] = mapped_column(Integer)

    # Phase 4: 헬스체크
    phase_4_health_check: Mapped[bool] = mapped_column(Boolean, default=False)
    phase_4_agent_statuses: Mapped[dict | None] = mapped_column(JSONB)

    # Phase 5: 매매 활성화
    phase_5_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    phase_5_enabled_strategies: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    # 메트릭
    total_boot_duration_ms: Mapped[int | None] = mapped_column(Integer)
    errors: Mapped[dict | None] = mapped_column(JSONB, default=list)
    system_version: Mapped[str | None] = mapped_column(String(50))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
