"""거시경제 분석 모듈.

Fear & Greed Index (alternative.me), BTC Dominance / 총 시총 (CoinGecko /global)
실시간 데이터를 수집한 뒤 LLM으로 종합 분석하여 시장 방향성을 판단한다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from src.core.llm.client import LLMResponse, Message, Role

logger = logging.getLogger(__name__)

LLMChatFn = Callable[[list[Message]], Awaitable[LLMResponse]]

_FEAR_GREED_URL = "https://api.alternative.me/fng/"
_COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"


class MacroAnalyzer:
    """거시경제 환경 분석."""

    def __init__(self, event_config: Any) -> None:
        self._event_cfg = event_config
        self.report: dict[str, Any] = {}
        self._macro_data: dict[str, Any] = {}

    async def analyze(self, llm_chat: LLMChatFn) -> dict[str, Any]:
        """실시간 데이터 수집 + LLM 분석."""
        await self._fetch_macro_data()

        system_prompt = (
            "You are a macroeconomic analyst tracking crypto market conditions. "
            "Synthesize the given REAL-TIME data into a concise market outlook.\n"
            "Respond with valid JSON only:\n"
            '{"market_direction": float(-1.0 to 1.0), "risk_level": str, '
            '"fear_greed_interpretation": str, "btc_dominance_interpretation": str, '
            '"narrative": str}'
        )

        data_section = self._format_macro_data()
        user_prompt = (
            "Analyze current crypto macro environment using the following "
            "REAL-TIME market data:\n\n"
            f"{data_section}\n\n"
            f"Fear & Greed extreme thresholds: "
            f"fear<={self._event_cfg.fear_greed.extreme_fear}, "
            f"greed>={self._event_cfg.fear_greed.extreme_greed}\n"
            "Provide your assessment."
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
        self.report["raw_data"] = self._macro_data
        return self.report

    async def _fetch_macro_data(self) -> None:
        """Fear & Greed + CoinGecko Global 데이터를 수집."""
        async with httpx.AsyncClient(timeout=15) as client:
            fg = await self._fetch_fear_greed(client)
            gl = await self._fetch_global_market(client)
        self._macro_data = {**fg, **gl}

    async def _fetch_fear_greed(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """alternative.me Fear & Greed Index."""
        try:
            resp = await client.get(_FEAR_GREED_URL, params={"limit": 1})
            resp.raise_for_status()
            entry = resp.json().get("data", [{}])[0]
            value = int(entry.get("value", 50))
            label = entry.get("value_classification", "Neutral")
            logger.info("Fear & Greed: %d (%s)", value, label)
            return {"fear_greed_value": value, "fear_greed_label": label}
        except Exception:
            logger.warning("Failed to fetch Fear & Greed Index", exc_info=True)
            return {"fear_greed_value": None, "fear_greed_label": "unavailable"}

    async def _fetch_global_market(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """CoinGecko /global — BTC dominance, 총 시총."""
        try:
            resp = await client.get(_COINGECKO_GLOBAL_URL)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            btc_dom = data.get("market_cap_percentage", {}).get("btc", 0)
            total_mcap = data.get("total_market_cap", {}).get("usd", 0)
            total_vol = data.get("total_volume", {}).get("usd", 0)
            mcap_chg = data.get("market_cap_change_percentage_24h_usd", 0)
            logger.info("Global: BTC dom=%.1f%% mcap=$%.0fB", btc_dom, total_mcap / 1e9 if total_mcap else 0)
            return {
                "btc_dominance_pct": round(btc_dom, 2),
                "total_market_cap_usd": total_mcap,
                "total_volume_24h_usd": total_vol,
                "market_cap_change_24h_pct": round(mcap_chg, 2),
            }
        except Exception:
            logger.warning("Failed to fetch CoinGecko global", exc_info=True)
            return {"btc_dominance_pct": None, "total_market_cap_usd": None}

    def _format_macro_data(self) -> str:
        """수집 데이터를 LLM 프롬프트용 텍스트로 포맷."""
        d = self._macro_data
        lines = []
        fg = d.get("fear_greed_value")
        if fg is not None:
            lines.append(f"- Fear & Greed Index: {fg} ({d.get('fear_greed_label', 'N/A')})")
        btc_dom = d.get("btc_dominance_pct")
        if btc_dom is not None:
            lines.append(f"- BTC Dominance: {btc_dom}%")
        mcap = d.get("total_market_cap_usd")
        if mcap:
            lines.append(f"- Total Crypto Market Cap: ${mcap / 1e9:.1f}B")
        vol = d.get("total_volume_24h_usd")
        if vol:
            lines.append(f"- 24H Total Volume: ${vol / 1e9:.1f}B")
        chg = d.get("market_cap_change_24h_pct")
        if chg is not None:
            lines.append(f"- Market Cap 24H Change: {chg:+.2f}%")
        return "\n".join(lines) if lines else "(No real-time data available)"
