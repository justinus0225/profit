"""전략 백테스트 모듈.

각 전략(Mean Reversion, Trend Following, Momentum, Breakout)의
과거 성과를 평가한다. 실시간 신호 결과를 누적하여
Win Rate, Avg Profit, Sharpe Ratio, MDD 등 핵심 지표를 계산한다.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from src.core.llm.client import LLMResponse, Message, Role

logger = logging.getLogger(__name__)

LLMChatFn = Callable[[list[Message]], Awaitable[LLMResponse]]

# 최소 평가 가능 신호 수
MIN_SIGNALS_FOR_EVAL = 5


class StrategyBacktester:
    """전략 성과 평가.

    신호 결과를 누적하고 전략별 성과 지표를 계산한다.
    """

    def __init__(self) -> None:
        # strategy_name → list of signal outcomes
        self._outcomes: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def record_outcome(self, outcome: dict[str, Any]) -> None:
        """신호 결과를 기록한다.

        Args:
            outcome: {"strategy", "symbol", "direction", "entry_price",
                      "exit_price", "profit_pct", "holding_hours", ...}
        """
        strategy = outcome.get("strategy", "unknown")
        self._outcomes[strategy].append(outcome)
        # 전략별 최근 200개만 유지
        if len(self._outcomes[strategy]) > 200:
            self._outcomes[strategy] = self._outcomes[strategy][-200:]

    async def evaluate_strategies(self, llm_chat: LLMChatFn) -> dict[str, Any]:
        """전략 성과를 평가한다.

        누적된 신호 결과로 정량 지표를 계산하고,
        LLM에 분석 컨텍스트를 전달하여 가중치 조정을 권고받는다.

        Returns:
            전략별 win_rate, avg_profit_pct, sharpe, mdd + LLM 권고.
        """
        strategies = ["mean_reversion", "trend_following", "momentum", "breakout"]
        metrics: dict[str, dict[str, Any]] = {}

        for name in strategies:
            outcomes = self._outcomes.get(name, [])
            metrics[name] = self._compute_metrics(name, outcomes)

        # LLM에 정량 지표 전달하여 종합 분석 요청
        metrics_summary = "\n".join(
            f"- {name}: signals={m['signal_count']}, win_rate={m['win_rate']:.1%}, "
            f"avg_profit={m['avg_profit_pct']:.2%}, sharpe={m['sharpe_ratio']:.2f}, "
            f"mdd={m['max_drawdown']:.2%}"
            for name, m in metrics.items()
        )

        response = await llm_chat([
            Message(
                role=Role.SYSTEM,
                content=(
                    "You are a quantitative strategy evaluator. "
                    "Based on real performance metrics, recommend weight adjustments. "
                    "Return JSON: {\"recommendations\": {\"strategy_name\": "
                    "{\"weight_adjustment\": float, \"rationale\": str}}}"
                ),
            ),
            Message(
                role=Role.USER,
                content=f"Strategy performance metrics:\n{metrics_summary}",
            ),
        ])

        return {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "metrics": metrics,
            "llm_recommendations": response.content,
        }

    @staticmethod
    def _compute_metrics(
        strategy_name: str, outcomes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """전략 성과 지표를 계산한다."""
        n = len(outcomes)
        if n < MIN_SIGNALS_FOR_EVAL:
            return {
                "strategy": strategy_name,
                "signal_count": n,
                "sufficient_data": False,
                "win_rate": 0.0,
                "avg_profit_pct": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "profit_factor": 0.0,
                "avg_holding_hours": 0.0,
            }

        profits = [o.get("profit_pct", 0) for o in outcomes]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]
        holdings = [o.get("holding_hours", 0) for o in outcomes]

        win_rate = len(wins) / n if n > 0 else 0
        avg_profit = sum(profits) / n if n > 0 else 0
        avg_holding = sum(holdings) / n if n > 0 else 0

        # Sharpe Ratio (연율화, 시간당 기준)
        if len(profits) > 1:
            mean_ret = sum(profits) / len(profits)
            var = sum((p - mean_ret) ** 2 for p in profits) / (len(profits) - 1)
            std_ret = math.sqrt(var) if var > 0 else 1e-10
            sharpe = (mean_ret / std_ret) * math.sqrt(365 * 24)  # 시간당 → 연율화
        else:
            sharpe = 0.0

        # Maximum Drawdown
        cumulative = 0.0
        peak = 0.0
        mdd = 0.0
        for p in profits:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = cumulative - peak
            if dd < mdd:
                mdd = dd

        # Profit Factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1e-10
        profit_factor = gross_profit / gross_loss

        return {
            "strategy": strategy_name,
            "signal_count": n,
            "sufficient_data": True,
            "win_rate": win_rate,
            "avg_profit_pct": avg_profit,
            "sharpe_ratio": sharpe,
            "max_drawdown": mdd,
            "profit_factor": profit_factor,
            "avg_holding_hours": avg_holding,
        }
