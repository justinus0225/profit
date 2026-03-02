"""P.R.O.F.I.T. FastAPI 진입점.

시스템 부팅 시:
1. ConfigManager, Redis, LLMRouter 초기화
2. ExchangeClient + RateLimiter 초기화
3. BootSequenceManager 6단계 실행
4. 에이전트 인스턴스 생성 + 초기화 + 백그라운드 실행
5. Redis → WebSocket 브리지 시작
6. REST API + WebSocket 엔드포인트 제공
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI

from src.agents.analyst import AnalystAgent
from src.agents.base import BaseAgent
from src.agents.engineer import DataEngineerAgent
from src.agents.executor.engine import ExecutorAgent
from src.agents.orchestrator import OrchestratorAgent
from src.agents.portfolio.manager import PortfolioManagerAgent
from src.agents.qa import QAAgent
from src.agents.quant import QuantAgent
from src.agents.risk.manager import RiskManagerAgent
from src.api.routes import agents, config, dashboard, signals, system, trading
from src.api.websocket.handlers import router as ws_router
from src.api.websocket.manager import RedisBridge, ws_manager
from src.core.boot_sequence import BootSequenceManager, BootStatus
from src.core.config import ConfigManager, ProfitConfig
from src.core.llm.router import LLMRouter
from src.exchange.client import ExchangeClient
from src.exchange.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# ── 전역 상태 (lifespan에서 초기화) ──
_config: ProfitConfig | None = None
_redis: aioredis.Redis | None = None
_llm_router: LLMRouter | None = None
_boot_status: BootStatus | None = None
_redis_bridge: RedisBridge | None = None
_bridge_task: asyncio.Task[None] | None = None
_exchange_client: ExchangeClient | None = None
_agents: list[BaseAgent] = []
_agent_tasks: list[asyncio.Task[None]] = []


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
    global _redis_bridge, _bridge_task, _exchange_client  # noqa: PLW0603
    global _agents, _agent_tasks  # noqa: PLW0603

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

    # 4) ExchangeClient + RateLimiter 초기화
    rate_limiter = RateLimiter(_redis, _config.exchange.rate_limit)
    _exchange_client = ExchangeClient(
        exchange_config=_config.exchange,
        execution_config=_config.execution,
        rate_limiter=rate_limiter,
        exchange_id=_config.exchange.exchange_id,
        paper_trading=_config.exchange.paper_trading or _config.system.paper_trading_mode,
    )
    await _exchange_client.initialize()
    logger.info("Exchange client initialized (%s)", _config.exchange.exchange_id)

    # 5) 부트 시퀀스 실행
    db_url = os.getenv("DATABASE_URL")
    boot_manager = BootSequenceManager(_config, _redis, db_url)
    _boot_status = await boot_manager.run()

    # 6) 에이전트 생성 + 초기화 + 백그라운드 실행
    _agents = _create_agents(_config, _llm_router, _redis, _exchange_client)
    for agent in _agents:
        await agent.initialize()
        task = asyncio.create_task(agent.run())
        _agent_tasks.append(task)
    logger.info("Agents started: %d", len(_agents))

    # 7) Redis → WebSocket 브리지 시작
    _redis_bridge = RedisBridge(ws_manager)
    bridge_redis = aioredis.from_url(redis_url, decode_responses=True)
    _bridge_task = asyncio.create_task(_redis_bridge.start(bridge_redis))

    logger.info(
        "P.R.O.F.I.T. ready (trading=%s, boot=%s, duration=%dms)",
        _config.system.trading_enabled,
        _boot_status.status,
        _boot_status.duration_ms,
    )

    yield

    # ── 종료 ──
    logger.info("P.R.O.F.I.T. shutting down...")

    # 에이전트 정지
    for agent in _agents:
        try:
            await agent.stop()
        except Exception:
            logger.exception("Error stopping agent %s", agent.name)
    for task in _agent_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _agents.clear()
    _agent_tasks.clear()

    # 거래소 연결 종료
    if _exchange_client:
        await _exchange_client.close()

    if _redis_bridge:
        _redis_bridge.stop()
    if _bridge_task:
        _bridge_task.cancel()
        try:
            await _bridge_task
        except asyncio.CancelledError:
            pass
    if _redis:
        await _redis.aclose()
    logger.info("P.R.O.F.I.T. stopped")


def _create_agents(
    cfg: ProfitConfig,
    llm_router: LLMRouter,
    redis_client: aioredis.Redis,
    exchange_client: ExchangeClient,
) -> list[BaseAgent]:
    """에이전트 인스턴스를 생성한다."""
    common = {
        "config": cfg,
        "llm_router": llm_router,
        "redis_client": redis_client,
    }

    return [
        AnalystAgent(
            name="analyst_macro",
            sub_type="analyst_macro",
            exchange_client=exchange_client,
            **common,
        ),
        QuantAgent(
            name="quant",
            exchange_client=exchange_client,
            **common,
        ),
        RiskManagerAgent(
            name="risk_manager",
            **common,
        ),
        PortfolioManagerAgent(
            name="portfolio_manager",
            **common,
        ),
        ExecutorAgent(
            name="executor",
            exchange_client=exchange_client,
            **common,
        ),
        DataEngineerAgent(
            name="data_engineer",
            exchange_client=exchange_client,
            **common,
        ),
        QAAgent(
            name="qa",
            **common,
        ),
        OrchestratorAgent(
            name="orchestrator",
            **common,
        ),
    ]


app = FastAPI(
    title="P.R.O.F.I.T.",
    description="Predictive Routing & Orchestration Framework for Intelligent Trading",
    version="0.1.0",
    lifespan=lifespan,
)

# ── REST API 라우터 등록 ──
app.include_router(dashboard.router)
app.include_router(trading.router)
app.include_router(config.router)
app.include_router(agents.router)
app.include_router(signals.router)
app.include_router(system.router)

# ── WebSocket 라우터 등록 ──
app.include_router(ws_router)


# ── 기본 엔드포인트 ──

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
        "websocket_connections": ws_manager.connection_count,
        "agents": len(_agents),
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
