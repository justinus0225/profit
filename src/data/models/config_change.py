"""설정 변경 감사 로그 모델."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models.base import Base, UUIDPrimaryKeyMixin


class ConfigChange(UUIDPrimaryKeyMixin, Base):
    """설정 변경 감사 로그.

    CONFIG_REFERENCE.md: 모든 설정 변경은 감사 로그에 기록된다.
    """

    __tablename__ = "config_changes"

    change_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    changed_by: Mapped[str] = mapped_column(String(100), nullable=False)

    # 변경 내용
    config_key: Mapped[str] = mapped_column(String(255), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)

    # 컨텍스트
    risk_level: Mapped[str | None] = mapped_column(String(20))
    change_reason: Mapped[str | None] = mapped_column(Text)

    # 검증
    validation_passed: Mapped[bool] = mapped_column(Boolean, default=True)
    validation_errors: Mapped[str | None] = mapped_column(Text)

    # 영향
    affected_agents: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    # Critical 설정 2차 확인
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 롤백
    rollback_available: Mapped[bool] = mapped_column(Boolean, default=True)
    rolled_back: Mapped[bool] = mapped_column(Boolean, default=False)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
