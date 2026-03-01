"""리스크 매니저 에이전트 - 자본 보존, 손실 통제, 거부권.

ARCHITECTURE.md: Level 2, Risk Manager
- 포지션 실시간 모니터링 (10초 간격)
- 리스크 점수 산출 (0~100) → 자본 활용률 결정
- 손절/트레일링 스탑 자동 실행
- 2-out-of-3 쿼럼에서 거부권 행사 가능
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


class RiskManagerAgent(BaseAgent):
    """리스크 매니저: 자본 보존 + 거부권."""

    @property
    def agent_type(self) -> str:
        return "risk"

    async def _on_initialize(self) -> None:
        self._fund_cfg = self._config.fund
        self._risk_cfg = self._config.risk
        self._schedule_cfg = self._config.schedule.risk

        # 상태
        self._risk_score: int = 0
        self._risk_level: str = "low"
        self._consecutive_losses: int = 0
        self._daily_realized_pnl: float = 0.0
        self._total_realized_pnl: float = 0.0
        self._positions: list[dict[str, Any]] = []

        # 이벤트 구독
        await self._subscribe("quant:signal", self._on_signal_received)
        await self._subscribe("orchestrator:consensus_check", self._on_consensus_check)
        await self._subscribe("portfolio:position_update", self._on_position_update)
        await self._subscribe("executor:order_filled", self._on_order_filled)

    async def _on_run(self) -> None:
        poll_interval = self._schedule_cfg.position_poll_seconds
        last_full_eval_date = ""

        while self._running:
            # 포지션 실시간 모니터링
            await self._monitor_positions()

            # 일일 전체 리스크 평가
            today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            if today != last_full_eval_date:
                current_time = datetime.now(tz=timezone.utc).strftime("%H:%M")
                if current_time >= self._schedule_cfg.full_eval_time:
                    await self._full_risk_evaluation()
                    last_full_eval_date = today
                    self._daily_realized_pnl = 0.0

            await asyncio.sleep(poll_interval)

    # ── 포지션 모니터링 ──

    async def _monitor_positions(self) -> None:
        """포지션 손절/트레일링 스탑 체크."""
        for pos in self._positions:
            current_price = pos.get("current_price", 0)
            entry_price = pos.get("entry_price", 0)
            if entry_price <= 0 or current_price <= 0:
                continue

            pnl_pct = (current_price - entry_price) / entry_price

            # 손절 체크
            if pnl_pct <= self._risk_cfg.default_stop_loss:
                await self._trigger_stop_loss(pos, pnl_pct)
                continue

            # 트레일링 스탑 체크
            highest = pos.get("highest_price", entry_price)
            if current_price > highest:
                pos["highest_price"] = current_price
            elif highest > 0:
                drawdown = (current_price - highest) / highest
                if drawdown <= -self._risk_cfg.trailing_stop:
                    await self._trigger_trailing_stop(pos, drawdown)

    async def _trigger_stop_loss(self, pos: dict[str, Any], loss_pct: float) -> None:
        """손절 트리거."""
        logger.warning("[%s] Stop-loss: %s at %.2f%% loss",
                        self.name, pos.get("symbol"), loss_pct * 100)
        await self._publish("risk:stop_loss_triggered", {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "symbol": pos.get("symbol"),
            "position_id": pos.get("position_id"),
            "price": pos.get("current_price"),
            "loss_pct": loss_pct,
            "position_quantity": pos.get("quantity", 0),
        })
        self._consecutive_losses += 1

    async def _trigger_trailing_stop(self, pos: dict[str, Any], drawdown: float) -> None:
        """트레일링 스탑 트리거."""
        logger.info("[%s] Trailing stop: %s drawdown %.2f%%",
                     self.name, pos.get("symbol"), drawdown * 100)
        await self._publish("risk:trailing_stop_triggered", {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "symbol": pos.get("symbol"),
            "position_id": pos.get("position_id"),
            "price": pos.get("current_price"),
            "profit_pct": (pos.get("current_price", 0) - pos.get("entry_price", 0)) / max(pos.get("entry_price", 1), 1),
            "position_quantity": pos.get("quantity", 0),
        })

    # ── 리스크 평가 ──

    async def _full_risk_evaluation(self) -> None:
        """일일 전체 리스크 점수 산출 (LLM 기반)."""
        logger.info("[%s] Full risk evaluation started", self.name)

        system_prompt = (
            "You are a risk manager. Calculate a comprehensive risk score.\n"
            "Respond with valid JSON:\n"
            '{"risk_score": int(0-100), "recommendation": str, '
            '"utilization_ratio": float, "action_flags": list[str]}'
        )

        user_prompt = (
            f"Current positions: {len(self._positions)}\n"
            f"Daily realized P&L: {self._daily_realized_pnl:.2%}\n"
            f"Total realized P&L: {self._total_realized_pnl:.2%}\n"
            f"Consecutive losses: {self._consecutive_losses}\n"
            f"Max consecutive losses: {self._risk_cfg.max_consecutive_losses}\n"
            f"Daily loss limit: {self._risk_cfg.daily_loss_limit}\n"
            f"Total loss limit: {self._risk_cfg.total_loss_limit}\n"
            f"Risk score: ? (0=min risk, 100=max risk)"
        )

        response = await self._llm_chat([
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_prompt),
        ])

        try:
            result = json.loads(response.content)
            old_score = self._risk_score
            self._risk_score = result.get("risk_score", self._risk_score)
            self._risk_level = self._score_to_level(self._risk_score)

            if self._score_to_level(old_score) != self._risk_level:
                await self._publish("risk:level_changed", {
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    "previous_level": self._score_to_level(old_score),
                    "new_level": self._risk_level,
                    "risk_score": self._risk_score,
                    "utilization_ratio": self._get_utilization(),
                })
        except json.JSONDecodeError:
            logger.warning("[%s] LLM returned non-JSON for risk eval", self.name)

    def _score_to_level(self, score: int) -> str:
        levels = self._risk_cfg.levels
        if score <= levels.low_max:
            return "low"
        if score <= levels.medium_max:
            return "medium"
        if score <= levels.high_max:
            return "high"
        return "critical"

    def _get_utilization(self) -> float:
        util = self._risk_cfg.utilization
        level = self._risk_level
        if level == "low":
            return util.low
        if level == "medium":
            return util.medium
        if level == "high":
            return util.high
        return 0.0  # critical → 투자 중단

    def _calculate_available_capital(self, total_balance: float) -> float:
        """투자 가능 자본 계산."""
        reserve = total_balance * self._fund_cfg.reserve_ratio
        available = total_balance - reserve
        return available * self._get_utilization()

    # ── 신호 검증 (거부권) ──

    def _check_veto_conditions(self, signal: dict[str, Any]) -> tuple[bool, str]:
        """거부권 조건 체크. (True, reason) → 거부."""
        # 일일 손실 한도 초과
        if self._daily_realized_pnl <= self._risk_cfg.daily_loss_limit:
            return True, "Daily loss limit exceeded"

        # 총 손실 한도 초과
        if self._total_realized_pnl <= self._risk_cfg.total_loss_limit:
            return True, "Total loss limit exceeded"

        # 연속 손실 초과
        if self._consecutive_losses >= self._risk_cfg.max_consecutive_losses:
            return True, f"Consecutive losses: {self._consecutive_losses}"

        # 리스크 레벨 Critical
        if self._risk_level == "critical":
            return True, "Risk level is CRITICAL"

        # 단일 포지션 한도 (max_single_position)
        max_coins = self._fund_cfg.max_concurrent_coins
        if len(self._positions) >= max_coins and signal.get("direction") == "BUY":
            return True, f"Max concurrent coins ({max_coins}) reached"

        return False, ""

    # ── 이벤트 핸들러 ──

    async def _on_signal_received(self, data: dict[str, Any]) -> None:
        """퀀트 신호 수신 → 리스크 검증."""
        vetoed, reason = self._check_veto_conditions(data)
        if vetoed:
            await self._publish("risk:rejected", {
                "signal_id": data.get("signal_id"),
                "rejection_reason": reason,
                "veto_flag": True,
                "risk_score": self._risk_score,
            })
            logger.warning("[%s] VETO: %s - %s", self.name, data.get("symbol"), reason)
        else:
            await self._publish("risk:approved", {
                "signal_id": data.get("signal_id"),
                "risk_level": self._risk_level,
                "risk_score": self._risk_score,
                "max_position_size_usd": self._fund_cfg.max_single_position,
                "veto_flag": False,
                "utilization_ratio": self._get_utilization(),
            })

    async def _on_consensus_check(self, data: dict[str, Any]) -> None:
        """오케스트레이터 합의 검증 요청."""
        vetoed, reason = self._check_veto_conditions(data)
        await self._publish("risk:approval_response", {
            "signal_id": data.get("signal_id"),
            "approval": not vetoed,
            "veto_flag": vetoed,
            "risk_score": self._risk_score,
            "risk_level": self._risk_level,
            "rejection_reason": reason if vetoed else "",
            "utilization_ratio": self._get_utilization(),
        })

    async def _on_position_update(self, data: dict[str, Any]) -> None:
        """포트폴리오 매니저로부터 포지션 업데이트 수신."""
        self._positions = data.get("positions", [])

    async def _on_order_filled(self, data: dict[str, Any]) -> None:
        """주문 체결 시 P&L 업데이트."""
        pnl = data.get("realized_pnl", 0)
        self._daily_realized_pnl += pnl
        self._total_realized_pnl += pnl
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
