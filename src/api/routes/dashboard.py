"""대시보드 API - 포트폴리오, 포지션, 성과.

ARCHITECTURE.md: Control Plane REST API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from src.api.schemas import (
    PerformanceResponse,
    PortfolioSummary,
    PositionsResponse,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/portfolio", response_model=PortfolioSummary)
async def get_portfolio() -> PortfolioSummary:
    """현재 포트폴리오 요약."""
    from src.api.deps import get_redis

    redis = get_redis()
    now = datetime.now(tz=timezone.utc).isoformat()

    # Redis에서 최신 포트폴리오 상태 조회
    portfolio_data: dict[str, Any] = {}
    if redis:
        raw = await redis.get("portfolio:summary")
        if raw:
            import json
            portfolio_data = json.loads(raw)

    return PortfolioSummary(
        timestamp=now,
        total_balance_usdt=portfolio_data.get("total_balance_usdt", 0),
        reserve_balance_usdt=portfolio_data.get("reserve_balance_usdt", 0),
        invested_balance_usdt=portfolio_data.get("invested_balance_usdt", 0),
        unrealized_pnl_usdt=portfolio_data.get("unrealized_pnl_usdt", 0),
        unrealized_pnl_pct=portfolio_data.get("unrealized_pnl_pct", 0),
        realized_pnl_usdt=portfolio_data.get("realized_pnl_usdt", 0),
        total_pnl_usdt=portfolio_data.get("total_pnl_usdt", 0),
        positions_count=portfolio_data.get("positions_count", 0),
        active_strategies=portfolio_data.get("active_strategies", []),
        risk_level=portfolio_data.get("risk_level", "low"),
        risk_score=portfolio_data.get("risk_score", 0),
    )


@router.get("/positions", response_model=PositionsResponse)
async def get_positions(
    symbol: str | None = Query(None, description="필터: 심볼"),
    status: str = Query("active", description="active|closed|all"),
    limit: int = Query(50, ge=1, le=200),
) -> PositionsResponse:
    """보유 포지션 목록."""
    from src.api.deps import get_redis

    redis = get_redis()
    now = datetime.now(tz=timezone.utc).isoformat()

    positions: list[dict[str, Any]] = []
    if redis:
        import json
        raw = await redis.get("portfolio:positions")
        if raw:
            all_positions = json.loads(raw)
            for pos in all_positions:
                if symbol and pos.get("symbol") != symbol:
                    continue
                if status != "all" and pos.get("status", "active") != status:
                    continue
                positions.append(pos)

    positions = positions[:limit]

    return PositionsResponse(
        positions=positions,
        total_count=len(positions),
        timestamp=now,
    )


@router.get("/performance", response_model=PerformanceResponse)
async def get_performance(
    period: str = Query("7d", description="1d|7d|30d|all"),
) -> PerformanceResponse:
    """성과 히스토리."""
    from src.api.deps import get_redis

    redis = get_redis()

    performance_data: dict[str, Any] = {}
    if redis:
        import json
        raw = await redis.get(f"portfolio:performance:{period}")
        if raw:
            performance_data = json.loads(raw)

    return PerformanceResponse(
        period=period,
        data=performance_data.get("data", []),
        summary=performance_data.get("summary", {}),
    )
