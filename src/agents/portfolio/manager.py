"""포트폴리오 매니저 에이전트 - 포트폴리오 구성, 리밸런싱, 보유기간 관리.

ARCHITECTURE.md: Level 2, Portfolio Manager
- 포지션 분류: 단기(1-7일), 중기(1-4주), 장기(1개월+)
- 일일 리밸런싱 (00:00 UTC): 보유기간 만료 포지션 연장/청산 판단
- 일일 성과 리포트 (09:00 UTC)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.agents.portfolio.rebalancer import Rebalancer
from src.agents.portfolio.sizing import PositionSizer

logger = logging.getLogger(__name__)


class PortfolioManagerAgent(BaseAgent):
    """포트폴리오 매니저: 구성 최적화 + 리밸런싱."""

    @property
    def agent_type(self) -> str:
        return "portfolio"

    async def _on_initialize(self) -> None:
        self._portfolio_cfg = self._config.portfolio
        self._schedule_cfg = self._config.schedule.portfolio

        # 현재 포트폴리오 상태
        self._positions: list[dict[str, Any]] = []
        self._risk_level: str = "low"

        # 모듈 초기화
        self._rebalancer = Rebalancer(self._portfolio_cfg)
        self._sizer = PositionSizer(self._portfolio_cfg)

        # 이벤트 구독
        await self._subscribe("orchestrator:consensus_approved", self._on_consensus_approved)
        await self._subscribe("quant:signal", self._on_signal)
        await self._subscribe("analyst:market_report", self._on_market_report)
        await self._subscribe("risk:level_changed", self._on_risk_level_changed)

    async def _on_run(self) -> None:
        last_rebalance_date = ""
        last_report_date = ""

        while self._running:
            now_utc = datetime.now(tz=timezone.utc)
            today = now_utc.strftime("%Y-%m-%d")
            current_time = now_utc.strftime("%H:%M")

            # 일일 리밸런싱 (00:00 UTC)
            if today != last_rebalance_date and current_time >= self._portfolio_cfg.rebalance_time:
                await self._rebalance()
                last_rebalance_date = today

            # 일일 성과 리포트 (09:00 UTC)
            if today != last_report_date and current_time >= self._schedule_cfg.report_time:
                await self._generate_report()
                last_report_date = today

            await asyncio.sleep(30)

    # ── 리밸런싱 ──

    async def _rebalance(self) -> None:
        """일일 리밸런싱: 보유기간 만료 포지션 연장/청산 판단."""
        logger.info("[%s] Rebalancing started (%d positions)",
                     self.name, len(self._positions))

        for pos in self._positions:
            if self._rebalancer.is_expired(pos):
                decision = await self._rebalancer.decide_extend_or_close(
                    pos, self._risk_level, self._llm_chat,
                )
                if decision.get("decision") == "extend":
                    await self._publish("portfolio:trade_approved", {
                        "action": "extend",
                        "symbol": pos.get("symbol"),
                        "position_id": pos.get("position_id"),
                        "new_holding_type": decision.get(
                            "new_holding_type", pos.get("holding_type")
                        ),
                        "rationale": decision.get("rationale", ""),
                    })
                else:
                    await self._publish("portfolio:rebalance_required", {
                        "action": "close",
                        "symbol": pos.get("symbol"),
                        "position_id": pos.get("position_id"),
                        "reason": decision.get("reason", ""),
                    })

        # 포트폴리오 배분 균형 체크
        self._rebalancer.check_allocation(self._positions)

    # ── 성과 리포트 ──

    async def _generate_report(self) -> None:
        """일일 성과 리포트 생성 및 발행."""
        logger.info("[%s] Generating daily report", self.name)
        total_pnl = sum(p.get("pnl_pct", 0) for p in self._positions)
        report = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "position_count": len(self._positions),
            "total_pnl_pct": total_pnl,
            "allocation": {
                ht: sum(1 for p in self._positions if p.get("holding_type") == ht)
                for ht in ("short_term", "mid_term", "long_term")
            },
        }
        await self._publish("portfolio:performance_report", report)

    # ── 이벤트 핸들러 ──

    async def _on_consensus_approved(self, data: dict[str, Any]) -> None:
        """합의 승인된 신호 → 포트폴리오 편입."""
        holding_period = data.get("holding_period", "short_term")
        max_days = {
            "short_term": self._portfolio_cfg.max_holding_days.short_term,
            "mid_term": self._portfolio_cfg.max_holding_days.mid_term,
        }
        logger.info("[%s] Consensus approved: %s (%s)",
                     self.name, data.get("symbol"), holding_period)
        await self._publish("portfolio:trade_approved", {
            "signal_id": data.get("signal_id"),
            "symbol": data.get("symbol"),
            "direction": data.get("direction"),
            "entry_price": data.get("entry_price"),
            "target_price": data.get("target_price"),
            "stop_loss_price": data.get("stop_loss_price"),
            "position_size_usd": data.get("position_size_usd", 0),
            "position_size_adjustment": data.get("position_size_adjustment", 1.0),
            "holding_type": holding_period,
            "max_holding_days": max_days.get(holding_period),
        })

    async def _on_signal(self, data: dict[str, Any]) -> None:
        """퀀트 신호 수신 (정보 저장용)."""

    async def _on_market_report(self, data: dict[str, Any]) -> None:
        """매크로 리포트 수신 (리밸런싱 참고)."""

    async def _on_risk_level_changed(self, data: dict[str, Any]) -> None:
        """리스크 레벨 변경 수신."""
        self._risk_level = data.get("new_level", self._risk_level)
        logger.info("[%s] Risk level changed to: %s", self.name, self._risk_level)
