"""주문 생성 모듈.

주문 객체 생성, 멱등성 키 부여, 주문 타입 결정.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.data.models.order import OrderState


class OrderBuilder:
    """주문 객체 생성기."""

    def __init__(self, execution_config: Any) -> None:
        self._cfg = execution_config

    def build(
        self,
        symbol: str,
        side: str,
        total_usd: float,
        price: float | None,
        signal: dict[str, Any],
        quantity: float | None = None,
    ) -> dict[str, Any]:
        """주문 객체를 생성한다.

        Args:
            symbol: 종목 심볼.
            side: "buy" 또는 "sell".
            total_usd: 주문 총액 (USD).
            price: 지정가. None이면 시장가.
            signal: 원본 신호 dict.
            quantity: 매도 시 수량.

        Returns:
            주문 dict (order_id, idempotency_key, state=CREATED 등).
        """
        idempotency_key = str(uuid.uuid4())
        return {
            "order_id": (
                f"ORD-{datetime.now(tz=timezone.utc).strftime('%Y%m%d%H%M%S')}"
                f"-{uuid.uuid4().hex[:6]}"
            ),
            "idempotency_key": idempotency_key,
            "symbol": symbol,
            "side": side,
            "order_type": self._cfg.default_order_type,
            "quantity": quantity,
            "total_usd": total_usd,
            "price": price,
            "signal_id": signal.get("signal_id"),
            "state": OrderState.CREATED.value,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
        }
