"""시그널 및 합의 API - 시그널 히스토리, 합의 메트릭.

ARCHITECTURE.md: Control Plane REST API.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from src.api.schemas import (
    ConsensusMetricsResponse,
    SignalInfo,
    SignalsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("/history", response_model=SignalsResponse)
async def get_signal_history(
    symbol: str | None = Query(None, description="심볼 필터"),
    strategy: str | None = Query(None, description="전략 필터"),
    status: str | None = Query(None, description="상태 필터"),
    limit: int = Query(50, ge=1, le=200),
) -> SignalsResponse:
    """시그널 히스토리 조회."""
    from src.api.deps import get_redis

    redis = get_redis()
    now = datetime.now(tz=timezone.utc).isoformat()

    signals: list[dict[str, Any]] = []
    if redis:
        raw = await redis.lrange("signals:history", 0, limit - 1)
        for item in raw:
            try:
                sig = json.loads(item)
                if symbol and sig.get("symbol") != symbol:
                    continue
                if strategy and sig.get("strategy") != strategy:
                    continue
                if status and sig.get("status") != status:
                    continue
                signals.append(sig)
            except json.JSONDecodeError:
                continue

    return SignalsResponse(
        signals=[SignalInfo(**s) for s in signals[:limit]],
        total=len(signals),
        timestamp=now,
    )


@router.get("/metrics", response_model=dict[str, Any])
async def get_signal_metrics(
    period: str = Query("7d", description="1d|7d|30d|all"),
) -> dict[str, Any]:
    """시그널 생성/승인 메트릭."""
    from src.api.deps import get_redis

    redis = get_redis()
    metrics: dict[str, Any] = {"period": period}

    if redis:
        raw = await redis.get(f"signals:metrics:{period}")
        if raw:
            metrics.update(json.loads(raw))

    return metrics


@router.get("/consensus/metrics", response_model=ConsensusMetricsResponse)
async def get_consensus_metrics() -> ConsensusMetricsResponse:
    """합의 메트릭 조회."""
    from src.api.deps import get_redis

    redis = get_redis()

    if redis:
        raw = await redis.get("consensus:metrics")
        if raw:
            data = json.loads(raw)
            return ConsensusMetricsResponse(**data)

    return ConsensusMetricsResponse()
