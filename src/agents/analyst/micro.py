"""개별 코인 펀더멘탈 분석 모듈.

CoinGecko API에서 시총, 거래량, GitHub 활동, 커뮤니티 데이터를
수집하여 LLM의 펀더멘탈 평가에 제공한다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from src.core.llm.client import LLMResponse, Message, Role

logger = logging.getLogger(__name__)

LLMChatFn = Callable[[list[Message]], Awaitable[LLMResponse]]

_COINGECKO_COIN_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}"


class MicroAnalyzer:
    """개별 코인 펀더멘탈 평가."""

    def __init__(self, screening_config: Any) -> None:
        self._cfg = screening_config

    async def _fetch_coin_data(self, coin_id: str) -> dict[str, Any]:
        """CoinGecko에서 개별 코인 상세 데이터를 조회한다."""
        if not coin_id:
            return {}
        try:
            url = _COINGECKO_COIN_URL.format(coin_id=coin_id.lower())
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params={
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "true",
                    "community_data": "true",
                    "developer_data": "true",
                    "sparkline": "false",
                })
                resp.raise_for_status()
                data = resp.json()
            market = data.get("market_data", {})
            dev = data.get("developer_data", {})
            comm = data.get("community_data", {})
            return {
                "market_cap_rank": data.get("market_cap_rank"),
                "market_cap_usd": market.get("market_cap", {}).get("usd"),
                "total_volume_usd": market.get("total_volume", {}).get("usd"),
                "price_change_24h_pct": market.get("price_change_percentage_24h"),
                "price_change_7d_pct": market.get("price_change_percentage_7d"),
                "price_change_30d_pct": market.get("price_change_percentage_30d"),
                "circulating_supply": market.get("circulating_supply"),
                "total_supply": market.get("total_supply"),
                "github_forks": dev.get("forks"),
                "github_stars": dev.get("stars"),
                "github_commit_count_4w": dev.get("commit_count_4_weeks"),
                "twitter_followers": comm.get("twitter_followers"),
                "reddit_subscribers": comm.get("reddit_subscribers"),
            }
        except Exception:
            logger.warning("Failed to fetch CoinGecko data for %s", coin_id)
            return {}

    async def score_coin(
        self, coin: dict[str, Any], llm_chat: LLMChatFn
    ) -> dict[str, Any]:
        """실제 데이터 + LLM 펀더멘탈 점수 산출."""
        coin_id = coin.get("coin_id", "")
        real_data = await self._fetch_coin_data(coin_id)

        system_prompt = (
            "You are a fundamental analyst evaluating cryptocurrency projects.\n"
            "Score the coin 0-100 with component breakdown:\n"
            "market_cap_rank(20), volume_market_cap(15), on_chain_activity(15), "
            "github_activity(15), sentiment(15), token_economics(10), exchange_liquidity(10)\n"
            "Respond with valid JSON:\n"
            '{"fundamental_score": int, "components": dict, "strengths": str, "risks": str}'
        )

        unlock_warn = self._cfg.unlock_warning
        data_section = self._format_coin_data(coin, real_data)
        user_prompt = (
            f"Coin: {coin.get('symbol', 'Unknown')}\n\n"
            f"REAL-TIME DATA:\n{data_section}\n\n"
            f"Token Unlock Warning: within {unlock_warn.days} days, "
            f"threshold {unlock_warn.ratio * 100}%\n"
            f"Evaluate fundamental score."
        )

        response = await llm_chat([
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_prompt),
        ])

        try:
            result = json.loads(response.content)
            result["real_data"] = real_data
            return result
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

    def _format_coin_data(self, coin: dict[str, Any], real: dict[str, Any]) -> str:
        """코인 데이터를 LLM 프롬프트용 텍스트로 포맷."""
        lines = []
        rank = real.get("market_cap_rank") or coin.get("market_cap_rank")
        if rank:
            lines.append(f"- Market Cap Rank: #{rank}")
        mcap = real.get("market_cap_usd")
        if mcap:
            lines.append(f"- Market Cap: ${mcap / 1e6:.1f}M")
        vol = real.get("total_volume_usd") or coin.get("daily_volume")
        if vol:
            lines.append(f"- 24H Volume: ${float(vol) / 1e6:.1f}M")
        for period, key in [("24H", "price_change_24h_pct"), ("7D", "price_change_7d_pct"), ("30D", "price_change_30d_pct")]:
            v = real.get(key)
            if v is not None:
                lines.append(f"- Price Change {period}: {v:+.2f}%")
        cs, ts = real.get("circulating_supply"), real.get("total_supply")
        if cs and ts and ts > 0:
            lines.append(f"- Supply: {cs / ts * 100:.1f}% circulating")
        commits = real.get("github_commit_count_4w")
        if commits is not None:
            lines.append(f"- GitHub: {commits} commits/4wk, {real.get('github_stars', 0)} stars")
        tw = real.get("twitter_followers")
        if tw:
            lines.append(f"- Twitter Followers: {tw:,}")
        return "\n".join(lines) if lines else "(No real-time data available)"
