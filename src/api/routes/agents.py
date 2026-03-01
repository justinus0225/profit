"""에이전트 모니터링 API - 상태, 헬스체크, 메트릭.

ARCHITECTURE.md: Control Plane REST API.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from src.api.schemas import AgentsStatusResponse, AgentStatusInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

# 예상 에이전트 목록
EXPECTED_AGENTS = [
    ("orchestrator", "Orchestrator"),
    ("quant", "Quant Agent"),
    ("analyst_macro", "Analyst (Macro)"),
    ("risk", "Risk Manager"),
    ("portfolio", "Portfolio Manager"),
    ("executor", "Execution Agent"),
]


@router.get("/status", response_model=AgentsStatusResponse)
async def get_agents_status() -> AgentsStatusResponse:
    """전체 에이전트 상태 조회."""
    from src.api.deps import get_redis

    redis = get_redis()
    now = datetime.now(tz=timezone.utc).isoformat()

    agents: list[AgentStatusInfo] = []
    all_heartbeats: dict[str, str] = {}
    if redis:
        all_heartbeats = await redis.hgetall("agent:heartbeat")

    running = 0
    for agent_id, agent_type_name in EXPECTED_AGENTS:
        raw = all_heartbeats.get(agent_id)
        status = "not_started"
        health = "unknown"
        last_heartbeat = None
        uptime = 0.0

        if raw:
            try:
                info = json.loads(raw)
                status = info.get("status", "unknown")
                last_heartbeat = info.get("timestamp")
                uptime = info.get("uptime_seconds", 0)
                health = "healthy" if status in ("running", "ready") else (
                    "degraded" if status == "warming" else "critical"
                )
                if status in ("running", "ready", "warming"):
                    running += 1
            except json.JSONDecodeError:
                pass

        agents.append(AgentStatusInfo(
            agent_id=agent_id,
            agent_type=agent_type_name,
            status=status,
            health=health,
            last_heartbeat=last_heartbeat,
            uptime_seconds=uptime,
        ))

    return AgentsStatusResponse(
        timestamp=now,
        agents=agents,
        summary={
            "total_agents": len(EXPECTED_AGENTS),
            "running_count": running,
            "healthy_count": running,
        },
    )


@router.get("/{agent_id}/metrics", response_model=dict[str, Any])
async def get_agent_metrics(agent_id: str) -> dict[str, Any]:
    """개별 에이전트 메트릭 조회."""
    from src.api.deps import get_redis

    redis = get_redis()

    metrics: dict[str, Any] = {"agent_id": agent_id}
    if redis:
        raw = await redis.get(f"agent:metrics:{agent_id}")
        if raw:
            metrics.update(json.loads(raw))

    return metrics
