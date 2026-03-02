"""소프트웨어 개발 에이전트 - 시스템 헬스 모니터링, 전략 코드 검증.

ARCHITECTURE.md: Level 1, Software Engineer Agent
- 거래소 API 연동 상태 모니터링
- 에이전트 헬스체크 종합
- 시스템 모듈 상태 검증
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class SoftwareEngineerAgent(BaseAgent):
    """소프트웨어 개발 에이전트: 시스템 상태 모니터링 + 헬스체크."""

    @property
    def agent_type(self) -> str:
        return "developer"

    async def _on_initialize(self) -> None:
        self._schedule_cfg = self._config.schedule

        await self._subscribe("executor:order_failed", self._on_order_failed)
        await self._subscribe("agent:status_changed", self._on_agent_status)

        self._error_counts: dict[str, int] = {}
        self._agent_statuses: dict[str, dict[str, Any]] = {}

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


__all__ = ["SoftwareEngineerAgent"]
