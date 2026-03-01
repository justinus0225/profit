"""트레이딩 컨트롤 API - 매매 활성/비활성, 수동 주문.

ARCHITECTURE.md: Control Plane REST API.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from src.api.schemas import (
    LiquidateRequest,
    LiquidateResponse,
    ManualOrderRequest,
    ManualOrderResponse,
    TradingToggleRequest,
    TradingToggleResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trading", tags=["trading"])


@router.post("/enable", response_model=TradingToggleResponse)
async def enable_trading(req: TradingToggleRequest) -> TradingToggleResponse:
    """매매 활성화."""
    from src.api.deps import get_config, get_redis

    config = get_config()
    redis = get_redis()
    now = datetime.now(tz=timezone.utc).isoformat()

    config.system.trading_enabled = True
    logger.info("Trading ENABLED (reason=%s)", req.reason)

    if redis:
        await redis.publish("system:trading_toggle", json.dumps({
            "trading_enabled": True,
            "reason": req.reason,
            "timestamp": now,
        }))

    return TradingToggleResponse(
        status="success",
        trading_enabled=True,
        timestamp=now,
    )


@router.post("/disable", response_model=TradingToggleResponse)
async def disable_trading(req: TradingToggleRequest) -> TradingToggleResponse:
    """매매 비활성화 (긴급 정지)."""
    from src.api.deps import get_config, get_redis

    config = get_config()
    redis = get_redis()
    now = datetime.now(tz=timezone.utc).isoformat()

    config.system.trading_enabled = False
    logger.warning("Trading DISABLED (reason=%s)", req.reason)

    if redis:
        await redis.publish("system:trading_toggle", json.dumps({
            "trading_enabled": False,
            "reason": req.reason,
            "timestamp": now,
        }))

    return TradingToggleResponse(
        status="success",
        trading_enabled=False,
        timestamp=now,
    )


@router.post("/manual-order", response_model=ManualOrderResponse)
async def submit_manual_order(req: ManualOrderRequest) -> ManualOrderResponse:
    """수동 주문 제출."""
    from src.api.deps import get_redis

    redis = get_redis()
    now = datetime.now(tz=timezone.utc).isoformat()
    order_id = f"ORD-{datetime.now(tz=timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

    if req.side == "BUY" and req.order_type == "limit" and req.price is None:
        raise HTTPException(status_code=400, detail="Limit order requires price")

    order: dict[str, Any] = {
        "order_id": order_id,
        "symbol": req.symbol,
        "side": req.side,
        "order_type": req.order_type,
        "price": req.price,
        "quantity": req.quantity,
        "reason": req.reason,
        "source": "manual_api",
        "timestamp": now,
    }

    logger.info("Manual order submitted: %s %s %s qty=%s",
                req.side, req.symbol, req.order_type, req.quantity)

    if redis:
        await redis.publish("orchestrator:execute_order", json.dumps(order))

    return ManualOrderResponse(
        order_id=order_id,
        symbol=req.symbol,
        side=req.side,
        status="submitted",
        quantity=req.quantity,
        price=req.price,
        timestamp=now,
    )


@router.post("/liquidate", response_model=LiquidateResponse)
async def liquidate_position(req: LiquidateRequest) -> LiquidateResponse:
    """특정 포지션 청산."""
    from src.api.deps import get_redis

    redis = get_redis()
    now = datetime.now(tz=timezone.utc).isoformat()

    logger.warning("Position liquidation requested: %s (reason=%s)",
                    req.position_id, req.reason)

    if redis:
        await redis.publish("portfolio:rebalance_required", json.dumps({
            "action": "close",
            "position_id": req.position_id,
            "reason": req.reason,
            "source": "manual_api",
            "timestamp": now,
        }))

    return LiquidateResponse(
        position_id=req.position_id,
        symbol="",
        status="liquidation_submitted",
        timestamp=now,
    )
