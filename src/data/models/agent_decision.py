"""에이전트 의사결정 감사 추적 모델."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models.base import Base, UUIDPrimaryKeyMixin


class AgentDecision(UUIDPrimaryKeyMixin, Base):
    """에이전트별 의사결정 감사 로그.

    모든 에이전트의 판단 근거, 입력, 출력을 기록한다.
    RAG 임베딩 대상 (embedded=True 후 pgvector 저장).
    """

    __tablename__ = "agent_decisions"

    # 에이전트 식별
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(50))

    # 의사결정 내용
    decision_type: Mapped[str] = mapped_column(String(100), nullable=False)
    decision_outcome: Mapped[str] = mapped_column(String(50), nullable=False)

    # 입출력
    input_data: Mapped[dict | None] = mapped_column(JSONB)
    output: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Float)

    # 합의 추적
    quorum_round_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # 성과 추적 (사후 라벨링)
    performance_label: Mapped[str | None] = mapped_column(String(50))
    realized_pnl_usd: Mapped[float | None] = mapped_column(Float)

    # RAG 임베딩 상태
    embedded: Mapped[bool] = mapped_column(Boolean, default=False)

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
