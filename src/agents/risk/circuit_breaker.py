"""서킷 브레이커 모듈.

연속 손실, 급격한 드로다운, 시장 급변 등의 조건에서
자동으로 거래를 중단시키는 안전 메커니즘.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """서킷 브레이커: 비상 거래 중단."""

    def __init__(self, risk_config: Any) -> None:
        self._cfg = risk_config
        self._triggered: bool = False
        self._trigger_time: datetime | None = None
        self._trigger_reason: str = ""

    @property
    def is_triggered(self) -> bool:
        """서킷 브레이커 작동 여부."""
        return self._triggered

    def check(self, state: dict[str, Any]) -> tuple[bool, str]:
        """서킷 브레이커 트리거 조건 체크.

        Args:
            state: daily_loss_pct, consecutive_losses 등을 포함한 상태.

        Returns:
            (should_trigger, reason) 튜플.
        """
        cb_cfg = self._cfg.circuit_breaker

        if state.get("daily_loss_pct", 0) <= cb_cfg.daily_loss_halt:
            return True, (
                f"Daily loss {state['daily_loss_pct']:.2%} "
                f"<= {cb_cfg.daily_loss_halt:.2%}"
            )

        if state.get("consecutive_losses", 0) >= cb_cfg.consecutive_loss_halt:
            return True, f"Consecutive losses: {state['consecutive_losses']}"

        return False, ""

    def trigger(self, reason: str) -> None:
        """서킷 브레이커를 작동시킨다."""
        self._triggered = True
        self._trigger_time = datetime.now(tz=timezone.utc)
        self._trigger_reason = reason
        logger.warning("Circuit breaker TRIGGERED: %s", reason)

    def reset(self) -> None:
        """서킷 브레이커를 해제한다."""
        self._triggered = False
        self._trigger_time = None
        self._trigger_reason = ""
        logger.info("Circuit breaker RESET")

    def status(self) -> dict[str, Any]:
        """현재 서킷 브레이커 상태."""
        return {
            "triggered": self._triggered,
            "trigger_time": (
                self._trigger_time.isoformat() if self._trigger_time else None
            ),
            "trigger_reason": self._trigger_reason,
        }
