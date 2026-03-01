"""DB 엔진 및 커넥션 풀 관리 (P13).

PgBouncer 경유 TimescaleDB 연결을 관리한다.
에이전트별 차등 커넥션 풀을 지원한다.
"""

from __future__ import annotations

import logging
import os

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.core.config import DBPoolConfig, ProfitConfig

logger = logging.getLogger(__name__)


def _build_url(config: DBPoolConfig) -> str:
    """환경변수 또는 config에서 DB URL을 구성한다."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    password = os.getenv("POSTGRES_PASSWORD", "profit_dev_password")
    return (
        f"postgresql+asyncpg://profit:{password}"
        f"@{config.pgbouncer_host}:{config.pgbouncer_port}/profit_db"
    )


def create_db_engine(
    config: ProfitConfig,
    agent_name: str | None = None,
) -> AsyncEngine:
    """비동기 SQLAlchemy 엔진을 생성한다.

    에이전트별 커넥션 풀 크기를 차등 적용한다 (P13).
    """
    pool_cfg = config.db.pool
    url = _build_url(pool_cfg)

    # 에이전트별 풀 크기 결정
    pool_size = pool_cfg.sqlalchemy_pool_size
    max_overflow = pool_cfg.sqlalchemy_max_overflow

    if agent_name and agent_name in pool_cfg.agent_pools:
        agent_pool = pool_cfg.agent_pools[agent_name]
        pool_size = agent_pool.pool_size
        max_overflow = agent_pool.max_overflow

    engine = create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_cfg.sqlalchemy_pool_timeout,
        pool_recycle=pool_cfg.sqlalchemy_pool_recycle,
        pool_pre_ping=True,
        echo=False,
    )

    logger.info(
        "DB engine created: agent=%s pool_size=%d max_overflow=%d",
        agent_name or "default",
        pool_size,
        max_overflow,
    )
    return engine


def create_session_factory(engine: AsyncEngine) -> sessionmaker:
    """비동기 세션 팩토리를 생성한다."""
    return sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
