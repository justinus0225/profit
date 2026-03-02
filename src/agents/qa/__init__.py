"""QA 에이전트 패키지.

에이전트 출력 품질 검증, Paper Trading 기반 신호 검증,
시스템 상태 정상성 모니터링, 전략 Shadow Testing 관리를 담당한다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.agents.qa.forward_test import ForwardTester
from src.agents.qa.validator import QAValidator
from src.agents.quant.shadow_tester import ShadowTester
from src.agents.quant.strategies.registry import (
    StrategyEntry,
    StrategyRegistry,
    StrategyStatus,
)

logger = logging.getLogger(__name__)


class QAAgent(BaseAgent):
    """QA 에이전트: 신호 품질 검증 + Paper Trading + Shadow Test 관리."""

    @property
    def agent_type(self) -> str:
        return "qa"

    async def _on_initialize(self) -> None:
        self._validator = QAValidator()
        self._forward_tester = ForwardTester(
            initial_balance=100_000.0,
        )

        # Shadow tester (독립 레지스트리로 SHADOW 전략 관리)
        self._shadow_registry = StrategyRegistry()
        evolution_cfg = getattr(self._config, "evolution", None)
        shadow_test_cfg = evolution_cfg.shadow if evolution_cfg else None
        self._shadow_tester = ShadowTester(
            registry=self._shadow_registry,
            config=shadow_test_cfg,
        )

        # 이벤트 구독
        await self._subscribe("quant:signal", self._on_signal)
        await self._subscribe(
            "orchestrator:consensus_approved", self._on_consensus_approved,
        )
        await self._subscribe("data:price_update", self._on_price_update)
        await self._subscribe("quant:strategy_shadow_start", self._on_shadow_start)

    async def _on_run(self) -> None:
        """주기적 QA 리포트 + Shadow 일일 평가."""
        report_interval = 3600  # 1시간
        last_shadow_eval_date = ""

        while self._running:
            # QA 리포트
            report = self._build_report()
            await self._publish("qa:report", report)
            logger.info(
                "[%s] QA report: validations=%d pass_rate=%.1f%% trades=%d shadow=%d",
                self.name,
                report["validation"]["total_validations"],
                report["validation"]["pass_rate"] * 100,
                report["forward_test"]["total_trades"],
                len(self._shadow_tester.active_sessions),
            )

            # Shadow 일일 평가
            now_utc = datetime.now(tz=timezone.utc)
            today = now_utc.strftime("%Y-%m-%d")
            eval_hour = 0
            evolution_cfg = getattr(self._config, "evolution", None)
            if evolution_cfg:
                eval_hour = evolution_cfg.shadow.evaluation_hour_utc
            if today != last_shadow_eval_date and now_utc.hour >= eval_hour:
                transitions = self._shadow_tester.evaluate_daily()
                for t in transitions:
                    if t["action"] == "promote":
                        await self._publish("quant:strategy_promoted", t)
                    elif t["action"] == "demote":
                        await self._publish("quant:strategy_demoted", t)
                last_shadow_eval_date = today

            await asyncio.sleep(report_interval)

    async def _on_signal(self, data: dict[str, Any]) -> None:
        """매매 신호 수신 → 품질 검증."""
        result = self._validator.validate_signal(data)

        if not result["valid"]:
            logger.warning(
                "[%s] Invalid signal %s: %s",
                self.name,
                data.get("signal_id"),
                result["errors"],
            )
            await self._publish("qa:signal_invalid", {
                "signal_id": data.get("signal_id"),
                "errors": result["errors"],
            })
            return

        # 유효한 신호 → Paper Trading 실행
        self._forward_tester.receive_signal(data)

    async def _on_consensus_approved(self, data: dict[str, Any]) -> None:
        """합의 승인된 신호 → 합의 검증."""
        self._validator.validate_consensus(data)

    async def _on_price_update(self, data: dict[str, Any]) -> None:
        """가격 업데이트 → 손절/목표가 체크."""
        prices = data.get("prices", {})
        if prices:
            self._forward_tester.check_stops(prices)
            self._shadow_tester.check_stops(prices)

    async def _on_shadow_start(self, data: dict[str, Any]) -> None:
        """QuantAgent로부터 Shadow 테스트 시작 요청 수신."""
        name = data.get("strategy_name", "")
        params = data.get("parameters", {})

        entry = StrategyEntry(
            name=name,
            status=StrategyStatus.SHADOW,
            parameters=params,
            source=data.get("source", "generated"),
        )
        self._shadow_registry.register(entry)
        self._shadow_tester.start_shadow(name)
        logger.info("[%s] Shadow test started: %s", self.name, name)

    def _build_report(self) -> dict[str, Any]:
        """종합 QA 리포트."""
        return {
            "validation": self._validator.get_stats(),
            "forward_test": self._forward_tester.get_performance(),
            "shadow_test": {
                "active_sessions": len(self._shadow_tester.active_sessions),
                "sessions": self._shadow_tester.get_session_status(),
            },
        }


__all__ = ["QAAgent"]
