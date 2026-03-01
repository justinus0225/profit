"""애널리스트 에이전트 - 시장 분석, 코인 선별, 감시 목록 관리.

ARCHITECTURE.md: Level 1, Research Analyst
3개 서브 에이전트:
- Macro: 거시경제 (Fed, DXY, Fear&Greed, BTC Dominance)
- Micro: 코인별 펀더멘탈 (시총, 깃허브, 온체인, 토큰 언락)
- Sentiment: 뉴스/소셜 감성 분석

2단계 코인 스크리닝 → 일일 감시 목록 생성.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.core.llm.client import Message, Role

logger = logging.getLogger(__name__)


class AnalystAgent(BaseAgent):
    """애널리스트 에이전트: 시장 환경 분석 + 코인 선별.

    sub_type 파라미터로 서브 에이전트 역할을 지정한다:
    - "analyst_macro": 거시경제 분석 + 1차 필터 + 감시 목록 조율
    - "analyst_micro": 코인별 펀더멘탈 점수 산출
    - "analyst_sentiment": 뉴스/소셜 감성 분석 (경량 LLM)
    """

    def __init__(self, *args: object, sub_type: str = "analyst_macro", **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._sub_type = sub_type

    @property
    def agent_type(self) -> str:
        return self._sub_type

    async def _on_initialize(self) -> None:
        self._screening_cfg = self._config.screening
        self._schedule_cfg = self._config.schedule.analyst
        self._event_cfg = self._config.event

        await self._subscribe("orchestrator:analysis_request", self._on_analysis_request)
        await self._subscribe("data:price_spike", self._on_price_spike)

        self._watchlist: list[dict[str, Any]] = []
        self._macro_report: dict[str, Any] = {}

    async def _on_run(self) -> None:
        news_interval = self._schedule_cfg.news_crawl_minutes * 60
        macro_interval = self._schedule_cfg.macro_update_minutes * 60

        last_news = last_macro = time.time()
        last_universe_date = ""

        while self._running:
            now = time.time()
            today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

            # 매크로 업데이트
            if now - last_macro >= macro_interval:
                await self._update_macro()
                last_macro = now

            # 뉴스 크롤링 + 감성 분석
            if now - last_news >= news_interval:
                await self._crawl_news_sentiment()
                last_news = now

            # 일일 감시 목록 갱신 (00:00 UTC)
            if today != last_universe_date:
                current_hour = datetime.now(tz=timezone.utc).strftime("%H:%M")
                target_time = self._schedule_cfg.universe_update_time
                if current_hour >= target_time:
                    await self._update_watchlist()
                    last_universe_date = today

            await asyncio.sleep(30)

    # ── 매크로 분석 ──

    async def _update_macro(self) -> None:
        """거시경제 환경 분석 (LLM 기반)."""
        logger.info("[%s] Macro analysis started", self.name)

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

        response = await self._llm_chat([
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_prompt),
        ])

        try:
            self._macro_report = json.loads(response.content)
        except json.JSONDecodeError:
            self._macro_report = {"raw": response.content}

        self._macro_report["timestamp"] = datetime.now(tz=timezone.utc).isoformat()
        await self._publish("analyst:market_report", self._macro_report)
        logger.info("[%s] Macro report: direction=%.2f risk=%s",
                     self.name,
                     self._macro_report.get("market_direction", 0),
                     self._macro_report.get("risk_level", "unknown"))

    # ── 뉴스 + 감성 분석 ──

    async def _crawl_news_sentiment(self) -> None:
        """뉴스 크롤링 및 감성 점수 산출 (외부 API 연동 예정)."""
        logger.info("[%s] News/sentiment crawl started", self.name)
        # 실제 뉴스 API (CoinGecko, CryptoCompare 등) 연동은 후속 구현
        await self._publish("analyst:sentiment_update", {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "sentiment_scores": {},
        })

    # ── 감시 목록 (2단계 스크리닝) ──

    async def _update_watchlist(self) -> None:
        """일일 감시 목록 갱신 (2단계 코인 필터링)."""
        logger.info("[%s] Watchlist screening started", self.name)

        # Stage 1: 펀더멘탈 필터 (시총 순위, 거래량, 블랙리스트)
        candidates = await self._stage1_filter()

        # Stage 2: 펀더멘탈 점수 (LLM 기반 종합 평가)
        scored = await self._stage2_scoring(candidates)

        # 최종 감시 목록
        min_score = self._screening_cfg.min_fundamental_score
        self._watchlist = [c for c in scored if c.get("fundamental_score", 0) >= min_score]
        self._watchlist.sort(key=lambda x: x.get("fundamental_score", 0), reverse=True)

        # 발행
        payload = {
            "selection_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            "coins": self._watchlist,
            "total_candidates": len(candidates),
            "total_selected": len(self._watchlist),
        }
        await self._publish("analyst:watchlist_updated", payload)
        logger.info("[%s] Watchlist: %d/%d coins selected (min_score=%d)",
                     self.name, len(self._watchlist), len(candidates), min_score)

    async def _stage1_filter(self) -> list[dict[str, Any]]:
        """Stage 1: 시총 순위 + 거래량 + 블랙리스트 필터."""
        # 실제 CoinGecko/CMC API 연동 예정. 현재는 프레임워크만 제공.
        cfg = self._screening_cfg
        candidates: list[dict[str, Any]] = []
        # 후속 구현: API에서 Top N 코인 조회
        # 필터: market_cap_rank <= cfg.market_cap_rank
        # 필터: daily_volume >= cfg.min_daily_volume
        # 필터: symbol not in cfg.blacklist
        # 추가: cfg.whitelist에 있으면 항상 포함
        logger.info("[%s] Stage 1 filter: market_cap<=%d, volume>=%s",
                     self.name, cfg.market_cap_rank, cfg.min_daily_volume)
        return candidates

    async def _stage2_scoring(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Stage 2: LLM 기반 펀더멘탈 점수 평가."""
        scored: list[dict[str, Any]] = []
        for coin in candidates:
            try:
                score_result = await self._score_coin(coin)
                coin["fundamental_score"] = score_result.get("fundamental_score", 0)
                coin["score_components"] = score_result.get("components", {})
                coin["strengths"] = score_result.get("strengths", "")
                coin["risks"] = score_result.get("risks", "")
                scored.append(coin)
            except Exception:
                logger.exception("[%s] Scoring error for %s", self.name, coin.get("symbol"))
        return scored

    async def _score_coin(self, coin: dict[str, Any]) -> dict[str, Any]:
        """개별 코인 펀더멘탈 점수 산출 (LLM)."""
        system_prompt = (
            "You are a fundamental analyst evaluating cryptocurrency projects.\n"
            "Score the coin 0-100 with component breakdown:\n"
            "market_cap_rank(20), volume_market_cap(15), on_chain_activity(15), "
            "github_activity(15), sentiment(15), token_economics(10), exchange_liquidity(10)\n"
            "Respond with valid JSON:\n"
            '{"fundamental_score": int, "components": dict, "strengths": str, "risks": str}'
        )

        # 토큰 언락 경고 체크
        unlock_warn = self._screening_cfg.unlock_warning
        user_prompt = (
            f"Coin: {coin.get('symbol', 'Unknown')}\n"
            f"Market Cap Rank: {coin.get('market_cap_rank', 'N/A')}\n"
            f"24H Volume: {coin.get('daily_volume', 'N/A')}\n"
            f"Token Unlock Warning: within {unlock_warn.days} days, "
            f"threshold {unlock_warn.ratio * 100}%\n"
            f"Evaluate fundamental score."
        )

        response = await self._llm_chat([
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_prompt),
        ])

        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            return {"fundamental_score": 0}

    # ── 이벤트 핸들러 ──

    async def _on_analysis_request(self, data: dict[str, Any]) -> None:
        """오케스트레이터 분석 요청 응답."""
        symbol = data.get("symbol", "")
        logger.info("[%s] Analysis request for %s", self.name, symbol)
        coin = {"symbol": symbol, "coin_id": data.get("coin_id")}
        score = await self._score_coin(coin)

        await self._publish("analyst:approval_response", {
            "signal_id": data.get("signal_id"),
            "approval": score.get("fundamental_score", 0) >= self._screening_cfg.min_fundamental_score,
            "market_direction_score": self._macro_report.get("market_direction", 0),
            "fundamental_score": score.get("fundamental_score", 0),
            "confidence": min(score.get("fundamental_score", 0), 100),
        })

    async def _on_price_spike(self, data: dict[str, Any]) -> None:
        """가격 급변 시 매크로 컨텍스트 제공."""
        logger.info("[%s] Price spike context check: %s", self.name, data.get("symbol"))
