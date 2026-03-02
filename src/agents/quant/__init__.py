"""퀀트 에이전트 패키지 - 기술적 분석 및 매매 신호 생성.

4개 전략(Mean Reversion, Trend Following, Momentum, Breakout) 기반 신호 생성.
스케줄: fast_scan(15분), deep_scan(60분), strategy_eval(240분).
이벤트: 가격/거래량 급변 시 긴급 분석.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.agents.quant.backtest import StrategyBacktester
from src.agents.quant.indicators import IndicatorEngine
from src.agents.quant.signals import SignalGenerator

logger = logging.getLogger(__name__)


class QuantAgent(BaseAgent):
    """퀀트 에이전트: 기술적 지표 기반 매매 신호 생성."""

    @property
    def agent_type(self) -> str:
        return "quant"

    async def _on_initialize(self) -> None:
        """전략 설정 로드 및 이벤트 구독."""
        self._strategy_cfg = self._config.strategy
        self._signal_cfg = self._config.signal
        self._schedule_cfg = self._config.schedule.quant

        # 모듈 초기화
        self._indicators = IndicatorEngine(self._strategy_cfg)
        self._signal_gen = SignalGenerator(self._signal_cfg, self._strategy_cfg)
        self._backtester = StrategyBacktester()

        # 이벤트 구독
        await self._subscribe("analyst:watchlist_updated", self._on_watchlist_updated)
        await self._subscribe("data:price_spike", self._on_price_spike)
        await self._subscribe("data:volume_spike", self._on_volume_spike)
        await self._subscribe("orchestrator:signal_request", self._on_signal_request)

        # 현재 감시 목록
        self._watchlist: list[dict[str, Any]] = []

    async def _on_run(self) -> None:
        """스케줄 기반 메인 루프."""
        fast_interval = self._schedule_cfg.fast_scan_minutes * 60
        deep_interval = self._schedule_cfg.deep_scan_minutes * 60
        eval_interval = self._schedule_cfg.strategy_eval_minutes * 60

        last_fast = last_deep = last_eval = time.time()

        while self._running:
            now = time.time()

            if now - last_fast >= fast_interval:
                await self._fast_scan()
                last_fast = now

            if now - last_deep >= deep_interval:
                await self._deep_scan()
                last_deep = now

            if now - last_eval >= eval_interval:
                result = await self._backtester.evaluate_strategies(self._llm_chat)
                await self._publish("quant:strategy_eval", result)
                last_eval = now

            await asyncio.sleep(10)

    # ── 스캔 루틴 ──

    async def _fast_scan(self) -> None:
        """빠른 기술적 스캔 (15분마다): RSI, MACD, BB 빠른 체크."""
        logger.info("[%s] Fast scan (%d symbols)", self.name, len(self._watchlist))
        for coin in self._watchlist:
            try:
                indicators = await self._indicators.compute(coin["symbol"], "1h")
                if indicators and self._indicators.exceeds_threshold(indicators):
                    await self._generate_signal(coin, indicators, "fast")
            except Exception:
                logger.exception(
                    "[%s] Fast scan error: %s", self.name, coin.get("symbol")
                )

    async def _deep_scan(self) -> None:
        """깊은 멀티 타임프레임 분석 (60분마다)."""
        logger.info("[%s] Deep scan started", self.name)
        for coin in self._watchlist:
            try:
                indicators_multi = {}
                for tf in ("1h", "4h", "1d"):
                    indicators_multi[tf] = await self._indicators.compute(
                        coin["symbol"], tf
                    )

                signal = await self._signal_gen.analyze(
                    coin, indicators_multi, self._llm_chat
                )
                if signal:
                    await self._publish("quant:signal", signal)
                    logger.info(
                        "[%s] Signal: %s %s (score=%s)",
                        self.name,
                        signal.get("direction"),
                        signal.get("symbol"),
                        signal.get("signal_score"),
                    )
            except Exception:
                logger.exception(
                    "[%s] Deep scan error: %s", self.name, coin.get("symbol")
                )

    async def _generate_signal(
        self, coin: dict[str, Any], indicators: dict[str, Any], scan_type: str
    ) -> None:
        """fast_scan에서 임계값 초과 시 LLM 기반 신호 발행."""
        signal = await self._signal_gen.analyze(
            coin, {"fast": indicators}, self._llm_chat
        )
        if not signal:
            return
        signal["scan_type"] = scan_type
        await self._publish("quant:signal", signal)
        logger.info(
            "[%s] Signal: %s %s (score=%s)",
            self.name,
            signal.get("direction"),
            signal.get("symbol"),
            signal.get("signal_score"),
        )

    # ── 이벤트 핸들러 ──

    async def _on_watchlist_updated(self, data: dict[str, Any]) -> None:
        """애널리스트 감시 목록 업데이트 수신."""
        self._watchlist = data.get("coins", [])
        logger.info("[%s] Watchlist updated: %d coins", self.name, len(self._watchlist))

    async def _on_price_spike(self, data: dict[str, Any]) -> None:
        """가격 급변 이벤트 수신 → 긴급 분석."""
        symbol = data.get("symbol", "")
        logger.warning(
            "[%s] Price spike: %s (%.2f%%)",
            self.name, symbol, data.get("change_pct", 0) * 100,
        )
        coin = {"symbol": symbol, "coin_id": data.get("coin_id")}
        indicators = await self._indicators.compute(symbol, "5m")
        if indicators:
            await self._generate_signal(coin, indicators, "rapid")
        await self._publish("quant:rapid_analysis", {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "trigger": "price_spike",
            **data,
        })

    async def _on_volume_spike(self, data: dict[str, Any]) -> None:
        """거래량 급증 이벤트 수신."""
        logger.info("[%s] Volume spike: %s", self.name, data.get("symbol"))

    async def _on_signal_request(self, data: dict[str, Any]) -> None:
        """오케스트레이터 분석 요청."""
        symbol = data.get("symbol", "")
        coin = {"symbol": symbol, "coin_id": data.get("coin_id")}
        indicators_multi = {}
        for tf in ("1h", "4h", "1d"):
            indicators_multi[tf] = await self._indicators.compute(symbol, tf)
        signal = await self._signal_gen.analyze(
            coin, indicators_multi, self._llm_chat
        )
        if signal:
            await self._publish("quant:signal", signal)


__all__ = ["QuantAgent"]
