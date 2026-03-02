"""거시경제 분석 모듈.

Fed 정책, DXY, Fear & Greed, BTC Dominance 등 거시 지표를
LLM으로 종합 분석하여 시장 방향성을 판단한다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from src.core.llm.client import LLMResponse, Message, Role

logger = logging.getLogger(__name__)

LLMChatFn = Callable[[list[Message]], Awaitable[LLMResponse]]


class MacroAnalyzer:
    """거시경제 환경 분석."""

    def __init__(self, event_config: Any) -> None:
        self._event_cfg = event_config
        self.report: dict[str, Any] = {}

    async def analyze(self, llm_chat: LLMChatFn) -> dict[str, Any]:
        """거시경제 환경을 LLM으로 분석한다.

        Returns:
            시장 방향성, 리스크 레벨, Fear&Greed 해석 등을 담은 dict.
        """
        system_prompt = (
            "You are a macroeconomic analyst tracking crypto market conditions. "
            "Synthesize the given data into a concise market outlook.\n"
            "Respond with valid JSON only:\n"
            '{"market_direction": float(-1.0 to 1.0), "risk_level": str, '
            '"fear_greed_interpretation": str, "btc_dominance_interpretation": str, '
            '"narrative": str}'
        )

        user_prompt = (
            "Analyze current crypto macro environment.\n"
            "Consider: Fed policy outlook, inflation trends, DXY strength, "
            "BTC dominance, stablecoin flows, Fear & Greed index.\n"
            f"Fear & Greed extreme thresholds: "
            f"fear<={self._event_cfg.fear_greed.extreme_fear}, "
            f"greed>={self._event_cfg.fear_greed.extreme_greed}"
        )

        response = await llm_chat([
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_prompt),
        ])

        try:
            self.report = json.loads(response.content)
        except json.JSONDecodeError:
            self.report = {"raw": response.content}

        self.report["timestamp"] = datetime.now(tz=timezone.utc).isoformat()
        return self.report
