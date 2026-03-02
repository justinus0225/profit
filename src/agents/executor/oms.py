"""주문 관리 시스템 (OMS) - 상태 머신 + 멱등성.

주문 상태 전이 관리:
  CREATED → SUBMITTED → FILLED | PARTIALLY_FILLED | CANCELLED
  PARTIALLY_FILLED → FILLED | CANCELLED
"""

from __future__ import annotations

import logging
from typing import Any

from src.data.models.order import VALID_TRANSITIONS, OrderState

logger = logging.getLogger(__name__)


class OrderStateMachine:
    """OMS 상태 전이 관리."""

    def transition(self, order: dict[str, Any], new_state_str: str) -> bool:
        """주문 상태를 전이한다.

        Args:
            order: 주문 dict (state 필드 포함).
            new_state_str: 새로운 상태 문자열.

        Returns:
            전이 성공 여부. 유효하지 않은 전이 시 False.
        """
        current = OrderState(order["state"])
        new_state = OrderState(new_state_str)

        if new_state not in VALID_TRANSITIONS.get(current, set()):
            logger.error("Invalid transition: %s → %s",
                          current.value, new_state.value)
            return False

        order["state"] = new_state.value
        return True
