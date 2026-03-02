"""리스크 한도 관리 모듈.

거부권 조건 체크, 자본 활용률 계산, 리스크 레벨 판정.
"""

from __future__ import annotations

from typing import Any


class RiskLimits:
    """리스크 한도 및 거부권 조건 관리."""

    def __init__(self, risk_config: Any, fund_config: Any) -> None:
        self._risk_cfg = risk_config
        self._fund_cfg = fund_config

    def score_to_level(self, score: int) -> str:
        """리스크 점수를 레벨로 변환한다."""
        levels = self._risk_cfg.levels
        if score <= levels.low_max:
            return "low"
        if score <= levels.medium_max:
            return "medium"
        if score <= levels.high_max:
            return "high"
        return "critical"

    def get_utilization(self, risk_level: str) -> float:
        """리스크 레벨에 따른 자본 활용률을 반환한다."""
        util = self._risk_cfg.utilization
        if risk_level == "low":
            return util.low
        if risk_level == "medium":
            return util.medium
        if risk_level == "high":
            return util.high
        return 0.0  # critical → 투자 중단

    def calculate_available_capital(
        self, total_balance: float, risk_level: str
    ) -> float:
        """투자 가능 자본을 계산한다."""
        reserve = total_balance * self._fund_cfg.reserve_ratio
        available = total_balance - reserve
        return available * self.get_utilization(risk_level)

    def check_veto(
        self, signal: dict[str, Any], state: dict[str, Any]
    ) -> tuple[bool, str]:
        """거부권 조건 체크. (True, reason) → 거부.

        Args:
            signal: 검증할 매매 신호.
            state: 현재 리스크 상태 (risk_level, consecutive_losses 등).

        Returns:
            (vetoed, reason) 튜플.
        """
        if state["daily_realized_pnl"] <= self._risk_cfg.daily_loss_limit:
            return True, "Daily loss limit exceeded"

        if state["total_realized_pnl"] <= self._risk_cfg.total_loss_limit:
            return True, "Total loss limit exceeded"

        if state["consecutive_losses"] >= self._risk_cfg.max_consecutive_losses:
            return True, f"Consecutive losses: {state['consecutive_losses']}"

        if state["risk_level"] == "critical":
            return True, "Risk level is CRITICAL"

        max_coins = self._fund_cfg.max_concurrent_coins
        if state["positions_count"] >= max_coins and signal.get("direction") == "BUY":
            return True, f"Max concurrent coins ({max_coins}) reached"

        return False, ""
