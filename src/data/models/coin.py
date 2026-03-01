"""코인(투자 유니버스) 모델."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.data.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Coin(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """투자 유니버스 코인 마스터 테이블."""

    __tablename__ = "coins"

    symbol: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    coingecko_id: Mapped[str | None] = mapped_column(String(50), unique=True)

    # 스크리닝 메타데이터
    market_cap_rank: Mapped[int | None] = mapped_column(Integer)
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    current_price_usd: Mapped[float | None] = mapped_column(Float)
    volume_24h_usd: Mapped[float | None] = mapped_column(Float)

    # 펀더멘탈 스코어 (0~100, 경제분석 에이전트)
    fundamental_score: Mapped[int] = mapped_column(Integer, default=0)
    last_fundamental_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 토큰 언락 경고
    token_unlock_warning: Mapped[bool] = mapped_column(Boolean, default=False)
    unlock_ratio: Mapped[float | None] = mapped_column(Float)
    unlock_days_remaining: Mapped[int | None] = mapped_column(Integer)

    # 블랙/화이트리스트
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_whitelisted: Mapped[bool] = mapped_column(Boolean, default=False)

    # 거래소 가용성
    trading_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    available_exchanges: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    # 관계
    positions: Mapped[list["Position"]] = relationship(back_populates="coin")  # noqa: F821
    orders: Mapped[list["Order"]] = relationship(back_populates="coin")  # noqa: F821
