"""포트폴리오 매니저 에이전트 - 포트폴리오 구성, 리밸런싱, 보유기간 관리.

ARCHITECTURE.md: Level 2, Portfolio Manager
- 포지션 분류: 단기(1-7일), 중기(1-4주), 장기(1개월+)
- 일일 리밸런싱 (00:00 UTC): 보유기간 만료 포지션 연장/청산 판단
- 일일 성과 리포트 (09:00 UTC)
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
        logger.info("[%s] Rebalancing started (%d positions)", self.name, len(self._positions))

        for pos in self._positions:
            if self._is_expired(pos):
                decision = await self._decide_extend_or_close(pos)
                if decision.get("decision") == "extend":
                    await self._extend_position(pos, decision)
                else:
                    await self._close_position(pos, decision)

        # 포트폴리오 배분 균형 체크
        await self._check_allocation_balance()

    def _is_expired(self, pos: dict[str, Any]) -> bool:
        """보유기간 만료 여부 확인."""
        target_close = pos.get("target_close_date")
        if not target_close:
            return False
        if isinstance(target_close, str):
            target_close = datetime.fromisoformat(target_close)
        return datetime.now(tz=timezone.utc) >= target_close.replace(tzinfo=timezone.utc)

    async def _decide_extend_or_close(self, pos: dict[str, Any]) -> dict[str, Any]:
        """LLM으로 포지션 연장/청산 판단."""
        extend_cfg = self._portfolio_cfg.extend_conditions
        pnl_pct = pos.get("pnl_pct", 0)
        fundamental_score = pos.get("fundamental_score", 0)

        # 조건 미충족 시 청산
        if pnl_pct < extend_cfg.min_pnl:
            return {"decision": "close", "reason": f"P&L {pnl_pct:.2%} < min {extend_cfg.min_pnl:.2%}"}
        if fundamental_score < extend_cfg.min_fundamental_score:
            return {"decision": "close", "reason": f"Fundamental {fundamental_score} < min {extend_cfg.min_fundamental_score}"}

        # 리스크 레벨 체크
        risk_levels = {"low": 20, "medium": 45, "high": 70, "critical": 90}
        current_risk = risk_levels.get(self._risk_level, 50)
        if current_risk > extend_cfg.max_risk_level:
            return {"decision": "close", "reason": f"Risk {self._risk_level} exceeds max"}

        # LLM 판단
        system_prompt = (
            "You are a portfolio manager deciding whether to extend or close a position.\n"
            "Respond with valid JSON:\n"
            '{"decision": "extend"|"close", "new_holding_type": str, '
            '"rationale": str}'
        )

        user_prompt = (
            f"Position: {pos.get('symbol')}\n"
            f"Holding type: {pos.get('holding_type', 'short_term')}\n"
            f"P&L: {pnl_pct:.2%}\n"
            f"Fundamental score: {fundamental_score}/100\n"
            f"Risk level: {self._risk_level}\n"
            f"Decision: Extend or Close?"
        )

        response = await self._llm_chat([
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_prompt),
        ])

        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            return {"decision": "close", "reason": "LLM response parse error"}

    async def _extend_position(self, pos: dict[str, Any], decision: dict[str, Any]) -> None:
        """포지션 연장."""
        new_type = decision.get("new_holding_type", pos.get("holding_type"))
        logger.info("[%s] Extending %s → %s", self.name, pos.get("symbol"), new_type)
        await self._publish("portfolio:trade_approved", {
            "action": "extend",
            "symbol": pos.get("symbol"),
            "position_id": pos.get("position_id"),
            "new_holding_type": new_type,
            "rationale": decision.get("rationale", ""),
        })

    async def _close_position(self, pos: dict[str, Any], decision: dict[str, Any]) -> None:
        """포지션 청산 요청."""
        logger.info("[%s] Closing %s: %s", self.name, pos.get("symbol"), decision.get("reason"))
        await self._publish("portfolio:rebalance_required", {
            "action": "close",
            "symbol": pos.get("symbol"),
            "position_id": pos.get("position_id"),
            "reason": decision.get("reason", ""),
        })

    async def _check_allocation_balance(self) -> None:
        """포트폴리오 배분 비율 체크 (단기/중기/장기)."""
        alloc = self._portfolio_cfg.allocation
        counts = {"short_term": 0, "mid_term": 0, "long_term": 0}
        total = len(self._positions) or 1
        for pos in self._positions:
            ht = pos.get("holding_type", "short_term")
            if ht in counts:
                counts[ht] += 1

        actual = {k: v / total for k, v in counts.items()}
        target = {
            "short_term": alloc.short_term,
            "mid_term": alloc.mid_term,
            "long_term": alloc.long_term,
        }
        logger.info("[%s] Allocation actual=%s target=%s", self.name, actual, target)

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
        pass

    async def _on_market_report(self, data: dict[str, Any]) -> None:
        """매크로 리포트 수신 (리밸런싱 참고)."""
        pass

    async def _on_risk_level_changed(self, data: dict[str, Any]) -> None:
        """리스크 레벨 변경 수신."""
        self._risk_level = data.get("new_level", self._risk_level)
        logger.info("[%s] Risk level changed to: %s", self.name, self._risk_level)
