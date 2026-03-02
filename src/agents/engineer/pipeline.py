"""데이터 파이프라인 관리 모듈.

데이터 수집 → 품질 검증 → 정상/힐링 → DB 삽입 흐름을 관리한다.
DataQualityPipeline을 에이전트 컨텍스트에서 실행한다.
"""

from __future__ import annotations

import logging
from typing import Any

from src.data.quality.pipeline import DataQualityPipeline, PipelineResult

logger = logging.getLogger(__name__)


class PipelineManager:
    """에이전트 레벨 데이터 파이프라인 관리."""

    def __init__(self, pipeline: DataQualityPipeline) -> None:
        self._pipeline = pipeline
        self._processed_count: int = 0
        self._accepted_count: int = 0
        self._rejected_count: int = 0
        self._halted_symbols: set[str] = set()

    def process_ohlcv(self, data: dict[str, Any]) -> PipelineResult:
        """OHLCV 데이터를 품질 파이프라인으로 처리한다."""
        result = self._pipeline.process_ohlcv(data)
        self._processed_count += 1

        if result.accepted:
            self._accepted_count += 1
        else:
            self._rejected_count += 1

        if result.halted:
            symbol = data.get("symbol", "")
            self._halted_symbols.add(symbol)
            logger.warning("Data collection halted for %s", symbol)

        return result

    def process_ticker(self, data: dict[str, Any]) -> PipelineResult:
        """실시간 틱 데이터를 품질 파이프라인으로 처리한다."""
        result = self._pipeline.process_ticker(data)
        self._processed_count += 1
        if result.accepted:
            self._accepted_count += 1
        else:
            self._rejected_count += 1
        return result

    def resume_symbol(self, symbol: str) -> None:
        """중단된 심볼의 수집을 재개한다."""
        self._pipeline.resume(symbol)
        self._halted_symbols.discard(symbol)
        logger.info("Data collection resumed for %s", symbol)

    def get_stats(self) -> dict[str, Any]:
        """파이프라인 처리 통계."""
        return {
            "processed": self._processed_count,
            "accepted": self._accepted_count,
            "rejected": self._rejected_count,
            "acceptance_rate": (
                self._accepted_count / self._processed_count
                if self._processed_count > 0
                else 0.0
            ),
            "halted_symbols": sorted(self._halted_symbols),
        }
