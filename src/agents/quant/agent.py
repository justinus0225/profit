"""퀀트 에이전트 - 기술적 분석 및 매매 신호 생성.

ARCHITECTURE.md: Level 1, Quantitative Trader
- 4개 전략(Mean Reversion, Trend Following, Momentum, Breakout) 기반 신호 생성
- 스케줄: fast_scan(15분), deep_scan(60분), strategy_eval(240분)
- 이벤트: 가격/거래량 급변 시 긴급 분석
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.core.llm.client import Message, Role

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
                await self._strategy_eval()
                last_eval = now

            await asyncio.sleep(10)

    # ── 스캔 루틴 ──

    async def _fast_scan(self) -> None:
        """빠른 기술적 스캔 (15분마다): RSI, MACD, BB 빠른 체크."""
        logger.info("[%s] Fast scan started (%d symbols)", self.name, len(self._watchlist))
        for coin in self._watchlist:
            try:
                indicators = await self._compute_indicators(coin["symbol"], "1h")
                if indicators and self._exceeds_threshold(indicators):
                    await self._generate_signal(coin, indicators, scan_type="fast")
            except Exception:
                logger.exception("[%s] Fast scan error for %s", self.name, coin.get("symbol"))

    async def _deep_scan(self) -> None:
        """깊은 멀티 타임프레임 분석 (60분마다)."""
        logger.info("[%s] Deep scan started", self.name)
        for coin in self._watchlist:
            try:
                indicators_multi = {}
                for tf in ("1h", "4h", "1d"):
                    indicators_multi[tf] = await self._compute_indicators(coin["symbol"], tf)

                signal = await self._llm_analyze_signal(coin, indicators_multi)
                if signal:
                    await self._publish_signal(signal)
            except Exception:
                logger.exception("[%s] Deep scan error for %s", self.name, coin.get("symbol"))

    async def _strategy_eval(self) -> None:
        """전략 성과 평가 (240분마다)."""
        logger.info("[%s] Strategy evaluation started", self.name)
        prompt = (
            "Review the performance of each enabled strategy "
            "(mean_reversion, trend_following, momentum, breakout) "
            "based on recent signal outcomes. "
            "Provide win_rate, avg_profit_pct, and recommended weight adjustments."
        )
        response = await self._llm_chat([
            Message(role=Role.SYSTEM, content="You are a quantitative strategy evaluator."),
            Message(role=Role.USER, content=prompt),
        ])
        await self._publish("quant:strategy_eval", {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "evaluation": response.content,
        })

    # ── 지표 계산 ──

    async def _compute_indicators(self, symbol: str, timeframe: str) -> dict[str, Any]:
        """기술적 지표를 계산한다 (pandas-ta 기반, 추후 구현 연동)."""
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "rsi_14": None,
            "macd_histogram": None,
            "bb_position": None,
            "ma_short": None,
            "ma_long": None,
            "adx": None,
            "atr": None,
            "volume_ratio": None,
        }

    def _exceeds_threshold(self, indicators: dict[str, Any]) -> bool:
        """빠른 스캔에서 임계값 초과 여부를 판단한다."""
        rsi = indicators.get("rsi_14")
        if rsi is None:
            return False
        cfg = self._strategy_cfg.mean_reversion
        return rsi <= cfg.rsi_oversold or rsi >= cfg.rsi_overbought

    # ── LLM 분석 ──

    async def _llm_analyze_signal(
        self, coin: dict[str, Any], indicators_multi: dict[str, dict[str, Any]]
    ) -> dict[str, Any] | None:
        """LLM으로 멀티 타임프레임 지표를 종합 분석하여 신호를 생성한다."""
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

        response = await self._llm_chat([
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_prompt),
        ])

        try:
            result = json.loads(response.content)
        except json.JSONDecodeError:
            logger.warning("[%s] LLM returned non-JSON for %s", self.name, symbol)
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

    # ── 신호 발행 ──

    async def _generate_signal(
        self, coin: dict[str, Any], indicators: dict[str, Any], scan_type: str
    ) -> None:
        """fast_scan에서 임계값 초과 시 간이 신호 발행."""
        signal = {
            "signal_id": f"SIG-{datetime.now(tz=timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "symbol": coin.get("symbol"),
            "scan_type": scan_type,
            "indicators": indicators,
        }
        await self._publish_signal(signal)

    async def _publish_signal(self, signal: dict[str, Any]) -> None:
        """생성된 신호를 quant:signal 채널에 발행한다."""
        await self._publish("quant:signal", signal)
        logger.info("[%s] Signal published: %s %s (score=%s)",
                     self.name, signal.get("direction", "?"), signal.get("symbol"),
                     signal.get("signal_score", "N/A"))

    # ── 이벤트 핸들러 ──

    async def _on_watchlist_updated(self, data: dict[str, Any]) -> None:
        """애널리스트 감시 목록 업데이트 수신."""
        self._watchlist = data.get("coins", [])
        logger.info("[%s] Watchlist updated: %d coins", self.name, len(self._watchlist))

    async def _on_price_spike(self, data: dict[str, Any]) -> None:
        """가격 급변 이벤트 수신 → 긴급 분석."""
        symbol = data.get("symbol", "")
        logger.warning("[%s] Price spike detected: %s (%.2f%%)",
                        self.name, symbol, data.get("change_pct", 0) * 100)
        coin = {"symbol": symbol, "coin_id": data.get("coin_id")}
        indicators = await self._compute_indicators(symbol, "5m")
        if indicators:
            await self._generate_signal(coin, indicators, scan_type="rapid")
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
            indicators_multi[tf] = await self._compute_indicators(symbol, tf)
        signal = await self._llm_analyze_signal(coin, indicators_multi)
        if signal:
            await self._publish_signal(signal)
