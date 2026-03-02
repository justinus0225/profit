"""데이터 엔지니어 에이전트 패키지.

데이터 파이프라인 관리, 스키마 관리, 데이터 품질 모니터링을 담당한다.
DataQualityPipeline을 활용한 실시간 데이터 품질 관리.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.agents.engineer.pipeline import PipelineManager
from src.agents.engineer.quality import QualityMonitor
from src.agents.engineer.schema import SchemaManager
from src.data.quality.pipeline import DataQualityPipeline

logger = logging.getLogger(__name__)


class DataEngineerAgent(BaseAgent):
    """데이터 엔지니어 에이전트: 데이터 파이프라인 + 품질 관리."""

    @property
    def agent_type(self) -> str:
        return "engineer"

    async def _on_initialize(self) -> None:
        dq_config = self._config.data_quality

        # 모듈 초기화
        raw_pipeline = DataQualityPipeline(dq_config)
        self._pipeline = PipelineManager(raw_pipeline)
        self._quality = QualityMonitor()
        self._schema = SchemaManager()

        # 이벤트 구독
        await self._subscribe("data:ohlcv_received", self._on_ohlcv_received)
        await self._subscribe("data:ticker_received", self._on_ticker_received)
        await self._subscribe("engineer:resume_collection", self._on_resume)

    async def _on_run(self) -> None:
        """주기적 품질 리포트 생성."""
        report_interval = 3600  # 1시간마다

        while self._running:
            report = self._quality.get_report()
            await self._publish("engineer:quality_report", report)
            logger.info(
                "[%s] Quality report: checks=%d anomaly_rate=%.2f%%",
                self.name,
                report["total_checks"],
                report["anomaly_rate"] * 100,
            )
            await asyncio.sleep(report_interval)

    async def _on_ohlcv_received(self, data: dict[str, Any]) -> None:
        """OHLCV 데이터 수신 → 품질 파이프라인 실행."""
        result = self._pipeline.process_ohlcv(data)

        for anomaly in result.anomalies:
            self._quality.record_anomaly(
                data.get("symbol", ""), anomaly.is_anomaly
            )
        for healing in result.healings:
            self._quality.record_healing(healing.success)

        if result.accepted:
            await self._publish("data:ohlcv_validated", result.data)
        elif result.halted:
            await self._publish("engineer:collection_halted", {
                "symbol": data.get("symbol"),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            })

    async def _on_ticker_received(self, data: dict[str, Any]) -> None:
        """실시간 틱 수신 → 품질 검증."""
        result = self._pipeline.process_ticker(data)
        if result.accepted:
            await self._publish("data:ticker_validated", result.data)

    async def _on_resume(self, data: dict[str, Any]) -> None:
        """중단된 심볼의 수집 재개."""
        symbol = data.get("symbol", "")
        self._pipeline.resume_symbol(symbol)
        logger.info("[%s] Collection resumed: %s", self.name, symbol)


__all__ = ["DataEngineerAgent"]
