"""시스템 상태 API - 시스템 상태, 알림, 부트 정보.

ARCHITECTURE.md: Control Plane REST API.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from src.api.schemas import AlertInfo, AlertsResponse, SystemStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status() -> SystemStatusResponse:
    """종합 시스템 상태."""
    from src.api.deps import get_boot_status, get_config, get_redis

    config = get_config()
    redis = get_redis()
    boot_status = get_boot_status()
    now = datetime.now(tz=timezone.utc).isoformat()

    system_info: dict[str, Any] = {
        "trading_enabled": config.system.trading_enabled if config else False,
        "paper_trading_mode": config.system.paper_trading_mode if config else True,
        "maintenance_mode": config.system.maintenance_mode if config else False,
    }

    boot_info: dict[str, Any] | None = None
    if boot_status:
        boot_info = {
            "session_id": str(boot_status.session_id),
            "status": boot_status.status,
            "duration_ms": boot_status.duration_ms,
            "enabled_strategies": boot_status.enabled_strategies,
        }

    resource_info: dict[str, Any] | None = None
    if redis:
        try:
            redis_info = await redis.info("memory")
            resource_info = {
                "redis_memory_mb": round(redis_info.get("used_memory", 0) / 1024 / 1024, 1),
                "redis_connected": True,
            }
        except Exception:
            resource_info = {"redis_connected": False}

    return SystemStatusResponse(
        timestamp=now,
        system=system_info,
        boot_sequence=boot_info,
        resource_usage=resource_info,
    )


@router.get("/alerts", response_model=AlertsResponse)
async def get_alerts(
    severity: str | None = Query(None, description="INFO|WARNING|CRITICAL"),
    limit: int = Query(100, ge=1, le=500),
) -> AlertsResponse:
    """시스템 알림 조회."""
    from src.api.deps import get_redis

    redis = get_redis()

    alerts: list[AlertInfo] = []
    if redis:
        raw_list = await redis.lrange("system:alerts", 0, limit - 1)
        for raw in raw_list:
            try:
                data = json.loads(raw)
                if severity and data.get("severity") != severity:
                    continue
                alerts.append(AlertInfo(**data))
            except (json.JSONDecodeError, Exception):
                continue

    summary = {
        "total": len(alerts),
        "critical": sum(1 for a in alerts if a.severity == "CRITICAL"),
        "warning": sum(1 for a in alerts if a.severity == "WARNING"),
        "info": sum(1 for a in alerts if a.severity == "INFO"),
    }

    return AlertsResponse(alerts=alerts, summary=summary)
