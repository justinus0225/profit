"""개별 코인 펀더멘탈 분석 모듈.

시총, GitHub 활동, 온체인, 토큰 이코노믹스, 거래소 유동성 등
개별 코인의 펀더멘탈을 LLM으로 평가하여 0-100 점수를 산출한다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.core.llm.client import LLMResponse, Message, Role

logger = logging.getLogger(__name__)

LLMChatFn = Callable[[list[Message]], Awaitable[LLMResponse]]


class MicroAnalyzer:
    """개별 코인 펀더멘탈 평가."""

    def __init__(self, screening_config: Any) -> None:
        self._cfg = screening_config

    async def score_coin(
        self, coin: dict[str, Any], llm_chat: LLMChatFn
    ) -> dict[str, Any]:
        """개별 코인 펀더멘탈 점수 산출 (LLM).

        Returns:
            fundamental_score(0-100), components, strengths, risks.
        """
        system_prompt = (
            "You are a fundamental analyst evaluating cryptocurrency projects.\n"
            "Score the coin 0-100 with component breakdown:\n"
            "market_cap_rank(20), volume_market_cap(15), on_chain_activity(15), "
            "github_activity(15), sentiment(15), token_economics(10), exchange_liquidity(10)\n"
            "Respond with valid JSON:\n"
            '{"fundamental_score": int, "components": dict, "strengths": str, "risks": str}'
        )

        unlock_warn = self._cfg.unlock_warning
        user_prompt = (
            f"Coin: {coin.get('symbol', 'Unknown')}\n"
            f"Market Cap Rank: {coin.get('market_cap_rank', 'N/A')}\n"
            f"24H Volume: {coin.get('daily_volume', 'N/A')}\n"
            f"Token Unlock Warning: within {unlock_warn.days} days, "
            f"threshold {unlock_warn.ratio * 100}%\n"
            f"Evaluate fundamental score."
        )

        response = await llm_chat([
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_prompt),
        ])

        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            return {"fundamental_score": 0}

    async def stage2_scoring(
        self, candidates: list[dict[str, Any]], llm_chat: LLMChatFn
    ) -> list[dict[str, Any]]:
        """Stage 2: LLM 기반 펀더멘탈 점수 평가."""
        scored: list[dict[str, Any]] = []
        for coin in candidates:
            try:
                score_result = await self.score_coin(coin, llm_chat)
                coin["fundamental_score"] = score_result.get("fundamental_score", 0)
                coin["score_components"] = score_result.get("components", {})
                coin["strengths"] = score_result.get("strengths", "")
                coin["risks"] = score_result.get("risks", "")
                scored.append(coin)
            except Exception:
                logger.exception("Scoring error for %s", coin.get("symbol"))
        return scored
