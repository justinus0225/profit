"""매매 신호 생성 모듈.

기술적 지표 분석 결과를 LLM으로 종합하여
방향성, 점수, 진입/청산 가격을 포함한 트레이딩 신호를 생성한다.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from src.core.llm.client import LLMResponse, Message, Role

logger = logging.getLogger(__name__)

LLMChatFn = Callable[[list[Message]], Awaitable[LLMResponse]]


class SignalGenerator:
    """LLM 기반 매매 신호 생성기."""

    def __init__(self, signal_config: Any, strategy_config: Any) -> None:
        self._signal_cfg = signal_config
        self._strategy_cfg = strategy_config

    async def analyze(
        self,
        coin: dict[str, Any],
        indicators_multi: dict[str, dict[str, Any]],
        llm_chat: LLMChatFn,
    ) -> dict[str, Any] | None:
        """LLM으로 멀티 타임프레임 지표를 종합 분석하여 신호를 생성한다.

        Returns:
            신호 dict (signal_id, direction, score 등) 또는 None (미달 시).
        """
        symbol = coin.get("symbol", "")
        indicators_text = json.dumps(indicators_multi, indent=2, default=str)

        system_prompt = (
            "You are a quantitative trader. Analyze the following technical indicators "
            "across multiple timeframes and provide a confidence-weighted trading signal.\n"
            "Respond with valid JSON only:\n"
            '{"score": int(-100 to +100), "confidence": int(0-100), '
            '"rationale": str, "strategy": str, "holding_period": str, '
            '"suggested_entry": float, "suggested_target": float, "suggested_stop_loss": float}'
        )

        user_prompt = (
            f"Symbol: {symbol}\n"
            f"Indicators (multi-timeframe):\n{indicators_text}\n\n"
            f"Buy threshold: {self._signal_cfg.buy_threshold}\n"
            f"Sell threshold: {self._signal_cfg.sell_threshold}\n"
            f"Enabled strategies: "
            f"mean_reversion={self._strategy_cfg.mean_reversion.enabled}, "
            f"trend_following={self._strategy_cfg.trend_following.enabled}, "
            f"momentum={self._strategy_cfg.momentum.enabled}, "
            f"breakout={self._strategy_cfg.breakout.enabled}"
        )

        response = await llm_chat([
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_prompt),
        ])

        try:
            result = json.loads(response.content)
        except json.JSONDecodeError:
            logger.warning("LLM returned non-JSON for %s", symbol)
            return None

        score = result.get("score", 0)
        if abs(score) < abs(self._signal_cfg.buy_threshold):
            return None

        return {
            "signal_id": f"SIG-{datetime.now(tz=timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "symbol": symbol,
            "coin_id": coin.get("coin_id"),
            "direction": "BUY" if score > 0 else "SELL",
            "signal_score": score,
            "confidence": result.get("confidence", 0),
            "strategy": result.get("strategy", ""),
            "entry_price": result.get("suggested_entry"),
            "target_price": result.get("suggested_target"),
            "stop_loss_price": result.get("suggested_stop_loss"),
            "holding_period": result.get("holding_period", "short_term"),
            "rationale": result.get("rationale", ""),
        }
