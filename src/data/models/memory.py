"""에이전트 메모리 임베딩 모델 (P11 RAG) - pgvector."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models.base import Base, UUIDPrimaryKeyMixin


class AgentMemoryEmbedding(UUIDPrimaryKeyMixin, Base):
    """에이전트 장기 메모리 임베딩 (pgvector).

    RAG 파이프라인에서 코사인 유사도 검색에 사용된다.
    벡터 차원: config llm_memory.embedding_dimension (기본 768).

    NOTE: pgvector의 Vector 타입은 Alembic 마이그레이션에서
    CREATE EXTENSION vector 후 사용한다. SQLAlchemy 모델에서는
    LargeBinary로 매핑하고 실제 DDL은 마이그레이션에서 처리한다.
    """

    __tablename__ = "agent_memory_embeddings"

    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # 원본 콘텐츠
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # 임베딩 벡터 - 실제 DDL에서 vector(768) 타입 적용
    # embedding 컬럼은 마이그레이션에서 직접 정의

    # 메타데이터
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)
    relevance_score: Mapped[float | None] = mapped_column(Float)

    # 수명 관리
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
