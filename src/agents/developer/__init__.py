"""소프트웨어 개발 에이전트 - 시스템 헬스 모니터링, 전략 코드 생성/검증.

ARCHITECTURE.md: Level 1, Software Engineer Agent
- 거래소 API 연동 상태 모니터링
- 에이전트 헬스체크 종합
- 시스템 모듈 상태 검증
- 전략 코드 생성 (evolution.generation_enabled=true 시)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class SoftwareEngineerAgent(BaseAgent):
    """소프트웨어 개발 에이전트: 시스템 상태 모니터링 + 헬스체크 + 코드 생성."""

    @property
    def agent_type(self) -> str:
        return "developer"

    async def _on_initialize(self) -> None:
        self._schedule_cfg = self._config.schedule

        await self._subscribe("executor:order_failed", self._on_order_failed)
        await self._subscribe("agent:status_changed", self._on_agent_status)
        await self._subscribe("quant:strategy_generate_request", self._on_generate_request)

        self._error_counts: dict[str, int] = {}
        self._agent_statuses: dict[str, dict[str, Any]] = {}

        # 전략 생성기 (lazy init)
        self._generator = None
        self._generation_registry = None

    async def _on_run(self) -> None:
        """주기적 시스템 헬스체크."""
        while self._running:
            await self._system_health_check()
            await asyncio.sleep(300)  # 5분마다

    async def _system_health_check(self) -> None:
        """시스템 상태를 수집하고 이상 시 알림을 발행한다."""
        report = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "agent_statuses": dict(self._agent_statuses),
            "error_counts": dict(self._error_counts),
            "exchange_connected": self._exchange_client is not None,
        }

        # 에러 임계값 초과 시 경고
        total_errors = sum(self._error_counts.values())
        if total_errors > 10:
            report["alert"] = "high_error_rate"
            logger.warning("[%s] High error rate: %d total errors", self.name, total_errors)

        await self._publish("developer:health_report", report)
        # 에러 카운터 주기적 리셋
        self._error_counts.clear()

    async def _on_order_failed(self, data: dict[str, Any]) -> None:
        """주문 실패 이벤트 추적."""
        symbol = data.get("symbol", "unknown")
        self._error_counts[f"order_failed:{symbol}"] = (
            self._error_counts.get(f"order_failed:{symbol}", 0) + 1
        )
        logger.warning("[%s] Order failed: %s — %s",
                        self.name, symbol, data.get("error", ""))

    async def _on_agent_status(self, data: dict[str, Any]) -> None:
        """에이전트 상태 변경 추적."""
        agent_type = data.get("type", "")
        self._agent_statuses[agent_type] = {
            "status": data.get("status"),
            "timestamp": data.get("timestamp"),
        }

    async def _on_generate_request(self, data: dict[str, Any]) -> None:
        """전략 코드 생성 요청 처리."""
        evolution_cfg = getattr(self._config, "evolution", None)
        if not evolution_cfg or not evolution_cfg.generation_enabled:
            logger.info("[%s] Strategy generation disabled", self.name)
            return

        # Lazy init
        if self._generator is None:
            from src.agents.quant.strategies.registry import StrategyRegistry
            from src.agents.quant.strategy_generator import StrategyGenerator

            self._generation_registry = StrategyRegistry()
            self._generator = StrategyGenerator(
                registry=self._generation_registry,
            )

        market_context = data.get("market_context", "")
        performance_context = data.get("performance_context", "")
        strategy_focus = data.get("strategy_focus", "adaptive trading strategy")

        logger.info("[%s] Generating strategy: %s", self.name, strategy_focus)

        try:
            entry = await self._generator.generate(
                llm_chat=self._llm_chat,
                market_context=market_context,
                performance_context=performance_context,
                strategy_focus=strategy_focus,
            )

            if entry:
                await self._publish("developer:strategy_generated", {
                    "strategy_name": entry.name,
                    "source": "generated",
                    "parameters": entry.parameters,
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                })
                logger.info("[%s] Strategy generated: %s", self.name, entry.name)
            else:
                await self._publish("developer:strategy_rejected", {
                    "reason": "generation_failed_or_safety_check",
                    "strategy_focus": strategy_focus,
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                })
        except Exception:
            logger.exception("[%s] Strategy generation error", self.name)
            await self._publish("developer:strategy_rejected", {
                "reason": "exception",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            })


__all__ = ["SoftwareEngineerAgent"]
