"""주문 체결 모니터링 모듈.

미체결 주문 타임아웃 체크, 거래소-OMS 동기화, 슬리피지 계산.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from src.data.models.order import OrderState

logger = logging.getLogger(__name__)


class OrderMonitor:
    """주문 체결 모니터링."""

    def __init__(self, execution_config: Any) -> None:
        self._cfg = execution_config

    def check_timeouts(
        self, pending_orders: dict[str, dict[str, Any]]
    ) -> list[str]:
        """미체결 주문 타임아웃 체크.

        Returns:
            타임아웃된 주문의 idempotency_key 목록.
        """
        timeout = self._cfg.limit_order_timeout
        now = time.time()
        expired: list[str] = []

        for key, order in pending_orders.items():
            if order.get("state") != OrderState.SUBMITTED.value:
                continue
            submitted = order.get("submitted_at", "")
            if not submitted:
                continue
            elapsed = now - datetime.fromisoformat(submitted).timestamp()
            if elapsed >= timeout:
                expired.append(key)
                logger.warning("Order timeout: %s %s (elapsed=%ds)",
                                order.get("symbol"), key[:8], timeout)

        return expired

    def reconcile(
        self, pending_orders: dict[str, dict[str, Any]]
    ) -> int:
        """거래소-OMS 상태 동기화 (미체결 주문 확인).

        Returns:
            미체결 주문 수.

        Note:
            실제 거래소 조회는 ExchangeClient 연동 시 구현 예정.
        """
        pending = [
            o for o in pending_orders.values()
            if o.get("state") in (
                OrderState.SUBMITTED.value, OrderState.PARTIALLY_FILLED.value
            )
        ]
        if pending:
            logger.info("Reconciliation: %d pending orders", len(pending))
        return len(pending)

    @staticmethod
    def calculate_slippage(expected_price: float, actual_price: float) -> float:
        """슬리피지 비율 계산."""
        if expected_price <= 0:
            return 0.0
        return (actual_price - expected_price) / expected_price
