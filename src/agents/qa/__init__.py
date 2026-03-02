"""QA 에이전트 패키지.

에이전트 출력 품질 검증, Paper Trading 기반 신호 검증,
시스템 상태 정상성 모니터링을 담당한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agents.base import BaseAgent
from src.agents.qa.forward_test import ForwardTester
from src.agents.qa.validator import QAValidator

logger = logging.getLogger(__name__)


class QAAgent(BaseAgent):
    """QA 에이전트: 신호 품질 검증 + Paper Trading."""

    @property
    def agent_type(self) -> str:
        return "qa"

    async def _on_initialize(self) -> None:
        self._validator = QAValidator()
        self._forward_tester = ForwardTester(
            initial_balance=self._config.fund.total_investment_limit,
        )

        # 이벤트 구독
        await self._subscribe("quant:signal", self._on_signal)
        await self._subscribe(
            "orchestrator:consensus_approved", self._on_consensus_approved,
        )
        await self._subscribe("data:price_update", self._on_price_update)

    async def _on_run(self) -> None:
        """주기적 QA 리포트 생성."""
        report_interval = 3600  # 1시간

        while self._running:
            report = self._build_report()
            await self._publish("qa:report", report)
            logger.info(
                "[%s] QA report: validations=%d pass_rate=%.1f%% trades=%d",
                self.name,
                report["validation"]["total_validations"],
                report["validation"]["pass_rate"] * 100,
                report["forward_test"]["total_trades"],
            )
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

    def _build_report(self) -> dict[str, Any]:
        """종합 QA 리포트."""
        return {
            "validation": self._validator.get_stats(),
            "forward_test": self._forward_tester.get_performance(),
        }


__all__ = ["QAAgent"]
