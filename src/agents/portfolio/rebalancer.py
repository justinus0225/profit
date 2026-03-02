"""포트폴리오 리밸런싱 모듈.

보유기간 만료 포지션 연장/청산 판단, 배분 비율 체크.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from src.core.llm.client import LLMResponse, Message, Role

logger = logging.getLogger(__name__)

LLMChatFn = Callable[..., Awaitable[LLMResponse]]


class Rebalancer:
    """포트폴리오 리밸런싱."""

    def __init__(self, portfolio_config: Any) -> None:
        self._cfg = portfolio_config

    def is_expired(self, pos: dict[str, Any]) -> bool:
        """보유기간 만료 여부 확인."""
        target_close = pos.get("target_close_date")
        if not target_close:
            return False
        if isinstance(target_close, str):
            target_close = datetime.fromisoformat(target_close)
        return datetime.now(tz=timezone.utc) >= target_close.replace(
            tzinfo=timezone.utc
        )

    async def decide_extend_or_close(
        self,
        pos: dict[str, Any],
        risk_level: str,
        llm_chat: LLMChatFn,
    ) -> dict[str, Any]:
        """LLM으로 포지션 연장/청산 판단.

        Args:
            pos: 포지션 정보 dict.
            risk_level: 현재 리스크 레벨.
            llm_chat: LLM 호출 callable.

        Returns:
            {"decision": "extend"|"close", ...} dict.
        """
        extend_cfg = self._cfg.extend_conditions
        pnl_pct = pos.get("pnl_pct", 0)
        fundamental_score = pos.get("fundamental_score", 0)

        # 조건 미충족 시 즉시 청산
        if pnl_pct < extend_cfg.min_pnl:
            return {
                "decision": "close",
                "reason": f"P&L {pnl_pct:.2%} < min {extend_cfg.min_pnl:.2%}",
            }
        if fundamental_score < extend_cfg.min_fundamental_score:
            return {
                "decision": "close",
                "reason": (
                    f"Fundamental {fundamental_score} "
                    f"< min {extend_cfg.min_fundamental_score}"
                ),
            }

        # 리스크 레벨 체크
        risk_levels = {"low": 20, "medium": 45, "high": 70, "critical": 90}
        current_risk = risk_levels.get(risk_level, 50)
        if current_risk > extend_cfg.max_risk_level:
            return {
                "decision": "close",
                "reason": f"Risk {risk_level} exceeds max",
            }

        # LLM 판단
        system_prompt = (
            "You are a portfolio manager deciding whether to extend or close a position.\n"
            "Respond with valid JSON:\n"
            '{"decision": "extend"|"close", "new_holding_type": str, '
            '"rationale": str}'
        )

        user_prompt = (
            f"Position: {pos.get('symbol')}\n"
            f"Holding type: {pos.get('holding_type', 'short_term')}\n"
            f"P&L: {pnl_pct:.2%}\n"
            f"Fundamental score: {fundamental_score}/100\n"
            f"Risk level: {risk_level}\n"
            f"Decision: Extend or Close?"
        )

        response = await llm_chat([
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_prompt),
        ])

        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            return {"decision": "close", "reason": "LLM response parse error"}

    def check_allocation(
        self, positions: list[dict[str, Any]]
    ) -> dict[str, float]:
        """포트폴리오 배분 비율 체크 (단기/중기/장기).

        Returns:
            실제 배분 비율 dict.
        """
        alloc = self._cfg.allocation
        counts = {"short_term": 0, "mid_term": 0, "long_term": 0}
        total = len(positions) or 1
        for pos in positions:
            ht = pos.get("holding_type", "short_term")
            if ht in counts:
                counts[ht] += 1

        actual = {k: v / total for k, v in counts.items()}
        target = {
            "short_term": alloc.short_term,
            "mid_term": alloc.mid_term,
            "long_term": alloc.long_term,
        }
        logger.info("Allocation actual=%s target=%s", actual, target)
        return actual
