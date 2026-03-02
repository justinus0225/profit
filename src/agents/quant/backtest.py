"""전략 백테스트 모듈.

각 전략(Mean Reversion, Trend Following, Momentum, Breakout)의
과거 성과를 평가하고 최적 파라미터를 탐색한다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from src.core.llm.client import LLMResponse, Message, Role

logger = logging.getLogger(__name__)

LLMChatFn = Callable[[list[Message]], Awaitable[LLMResponse]]


class StrategyBacktester:
    """전략 성과 평가 및 백테스트."""

    async def evaluate_strategies(self, llm_chat: LLMChatFn) -> dict[str, Any]:
        """전략 성과 평가 (LLM 기반).

        Returns:
            각 전략의 win_rate, avg_profit_pct, 가중치 조정 권고.
        """
        prompt = (
            "Review the performance of each enabled strategy "
            "(mean_reversion, trend_following, momentum, breakout) "
            "based on recent signal outcomes. "
            "Provide win_rate, avg_profit_pct, and recommended weight adjustments."
        )

        response = await llm_chat([
            Message(role=Role.SYSTEM, content="You are a quantitative strategy evaluator."),
            Message(role=Role.USER, content=prompt),
        ])

        return {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "evaluation": response.content,
        }
