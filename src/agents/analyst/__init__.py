"""애널리스트 에이전트 패키지 - 시장 분석, 코인 선별, 감시 목록 관리.

3개 서브 에이전트:
- Macro: 거시경제 (Fed, DXY, Fear&Greed, BTC Dominance)
- Micro: 코인별 펀더멘탈 (시총, 깃허브, 온체인, 토큰 언락)
- Sentiment: 뉴스/소셜 감성 분석

2단계 코인 스크리닝 → 일일 감시 목록 생성.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from src.agents.analyst.macro import MacroAnalyzer
from src.agents.analyst.micro import MicroAnalyzer
from src.agents.analyst.screener import CoinScreener
from src.agents.analyst.sentiment import SentimentAnalyzer
from src.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class AnalystAgent(BaseAgent):
    """애널리스트 에이전트: 시장 환경 분석 + 코인 선별.

    sub_type 파라미터로 서브 에이전트 역할을 지정한다:
    - "analyst_macro": 거시경제 분석 + 1차 필터 + 감시 목록 조율
    - "analyst_micro": 코인별 펀더멘탈 점수 산출
    - "analyst_sentiment": 뉴스/소셜 감성 분석 (경량 LLM)
    """

    def __init__(
        self, *args: object, sub_type: str = "analyst_macro", **kwargs: object
    ) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._sub_type = sub_type

    @property
    def agent_type(self) -> str:
        return self._sub_type

    async def _on_initialize(self) -> None:
        self._screening_cfg = self._config.screening
        self._schedule_cfg = self._config.schedule.analyst
        self._event_cfg = self._config.event

        # 모듈 초기화
        self._macro = MacroAnalyzer(self._event_cfg)
        self._micro = MicroAnalyzer(self._screening_cfg)
        self._sentiment = SentimentAnalyzer()
        self._screener = CoinScreener(self._screening_cfg)

        await self._subscribe(
            "orchestrator:analysis_request", self._on_analysis_request
        )
        await self._subscribe("data:price_spike", self._on_price_spike)

        self._watchlist: list[dict[str, Any]] = []

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
                report = await self._macro.analyze(self._llm_chat)
                await self._publish("analyst:market_report", report)
                logger.info(
                    "[%s] Macro: direction=%.2f risk=%s",
                    self.name,
                    report.get("market_direction", 0),
                    report.get("risk_level", "unknown"),
                )
                last_macro = now

            # 뉴스 크롤링 + 감성 분석
            if now - last_news >= news_interval:
                result = await self._sentiment.crawl()
                await self._publish("analyst:sentiment_update", result)
                last_news = now

            # 일일 감시 목록 갱신
            if today != last_universe_date:
                current_hour = datetime.now(tz=timezone.utc).strftime("%H:%M")
                target_time = self._schedule_cfg.universe_update_time
                if current_hour >= target_time:
                    await self._update_watchlist()
                    last_universe_date = today

            await asyncio.sleep(30)

    # ── 감시 목록 (2단계 스크리닝) ──

    async def _update_watchlist(self) -> None:
        """일일 감시 목록 갱신 (2단계 코인 필터링)."""
        logger.info("[%s] Watchlist screening started", self.name)

        # Stage 1: 펀더멘탈 필터
        candidates = await self._screener.stage1_filter()

        # Stage 2: LLM 기반 펀더멘탈 점수
        scored = await self._micro.stage2_scoring(candidates, self._llm_chat)

        # 최소 점수 필터 + 정렬
        self._watchlist = self._screener.apply_min_score_filter(scored)

        # 발행
        payload = {
            "selection_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            "coins": self._watchlist,
            "total_candidates": len(candidates),
            "total_selected": len(self._watchlist),
        }
        await self._publish("analyst:watchlist_updated", payload)
        logger.info(
            "[%s] Watchlist: %d/%d coins selected",
            self.name, len(self._watchlist), len(candidates),
        )

    # ── 이벤트 핸들러 ──

    async def _on_analysis_request(self, data: dict[str, Any]) -> None:
        """오케스트레이터 분석 요청 응답."""
        symbol = data.get("symbol", "")
        logger.info("[%s] Analysis request for %s", self.name, symbol)
        coin = {"symbol": symbol, "coin_id": data.get("coin_id")}
        score = await self._micro.score_coin(coin, self._llm_chat)

        await self._publish("analyst:approval_response", {
            "signal_id": data.get("signal_id"),
            "approval": (
                score.get("fundamental_score", 0)
                >= self._screening_cfg.min_fundamental_score
            ),
            "market_direction_score": self._macro.report.get(
                "market_direction", 0
            ),
            "fundamental_score": score.get("fundamental_score", 0),
            "confidence": min(score.get("fundamental_score", 0), 100),
        })

    async def _on_price_spike(self, data: dict[str, Any]) -> None:
        """가격 급변 시 매크로 컨텍스트 제공."""
        logger.info(
            "[%s] Price spike context check: %s",
            self.name, data.get("symbol"),
        )


__all__ = ["AnalystAgent"]
