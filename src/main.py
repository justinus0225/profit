"""P.R.O.F.I.T. FastAPI 진입점.

시스템 부팅 시:
1. ConfigManager, Redis, LLMRouter 초기화
2. BootSequenceManager 6단계 실행
3. 헬스체크 및 메트릭 엔드포인트 제공
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI

from src.core.boot import BootSequenceManager, BootStatus
from src.core.config import ConfigManager, ProfitConfig
from src.core.llm.router import LLMRouter

logger = logging.getLogger(__name__)

# ── 전역 상태 (lifespan에서 초기화) ──
_config: ProfitConfig | None = None
_redis: aioredis.Redis | None = None
_llm_router: LLMRouter | None = None
_boot_status: BootStatus | None = None


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    """애플리케이션 시작/종료 시 리소스를 관리한다."""
    global _config, _redis, _llm_router, _boot_status  # noqa: PLW0603

    # ── 시작 ──
    import os

    log_level = os.getenv("LOG_LEVEL", "INFO")
    _setup_logging(log_level)
    logger.info("P.R.O.F.I.T. starting...")

    # 1) 설정 로딩
    cm = ConfigManager()
    _config = cm.config
    logger.info("Config loaded (paper_trading=%s)", _config.system.paper_trading_mode)

    # 2) Redis 연결
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    _redis = aioredis.from_url(redis_url, decode_responses=True)
    await _redis.ping()
    logger.info("Redis connected")

    # 3) LLM Router 초기화
    _llm_router = LLMRouter(_config.llm)
    logger.info("LLM Router initialized (provider=%s)", _config.llm.default_provider)

    # 4) 부트 시퀀스 실행
    db_url = os.getenv("DATABASE_URL")
    boot_manager = BootSequenceManager(_config, _redis, db_url)
    _boot_status = await boot_manager.run()

    logger.info(
        "P.R.O.F.I.T. ready (trading=%s, boot=%s, duration=%dms)",
        _config.system.trading_enabled,
        _boot_status.status,
        _boot_status.duration_ms,
    )

    yield

    # ── 종료 ──
    logger.info("P.R.O.F.I.T. shutting down...")
    if _redis:
        await _redis.aclose()
    logger.info("P.R.O.F.I.T. stopped")


app = FastAPI(
    title="P.R.O.F.I.T.",
    description="Predictive Routing & Orchestration Framework for Intelligent Trading",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    """시스템 헬스체크."""
    redis_ok = False
    if _redis:
        try:
            await _redis.ping()
            redis_ok = True
        except Exception:
            pass

    return {
        "status": "ok" if redis_ok else "degraded",
        "config_loaded": _config is not None,
        "redis": "connected" if redis_ok else "disconnected",
        "llm_router": _llm_router is not None,
        "paper_trading": _config.system.paper_trading_mode if _config else None,
        "trading_enabled": _config.system.trading_enabled if _config else None,
    }


@app.get("/boot")
async def boot_info() -> dict[str, Any]:
    """부트 시퀀스 상태 조회."""
    if not _boot_status:
        return {"status": "not_booted"}

    return {
        "session_id": str(_boot_status.session_id),
        "status": _boot_status.status,
        "duration_ms": _boot_status.duration_ms,
        "enabled_strategies": _boot_status.enabled_strategies,
        "agent_statuses": _boot_status.agent_statuses,
        "phases": {
            str(k): {
                "success": v.success,
                "duration_ms": v.duration_ms,
                "data": v.data,
            }
            for k, v in _boot_status.phases.items()
        },
        "errors": _boot_status.errors,
    }
