"""데이터 품질 파이프라인 통합 모듈 (ARCHITECTURE.md P10).

3단계를 하나로 통합:
1. AnomalyDetector (이상치 탐지)
2. DataHealer (힐링)
3. DataValidator (삽입 전 검증)

데이터 수집 → process() → 정상/힐링 데이터 반환 또는 수집 중단.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.core.config import DataQualityConfig
from src.data.quality.anomaly_detector import AnomalyDetector, AnomalyResult
from src.data.quality.data_healer import DataHealer, HealingResult
from src.data.quality.validator import DataValidator, ValidationResult

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """파이프라인 처리 결과."""

    accepted: bool  # 데이터 삽입 가능 여부
    data: dict[str, Any]  # 최종 데이터 (힐링 적용된 경우 수정된 값)
    validation: ValidationResult | None
    anomalies: list[AnomalyResult]
    healings: list[HealingResult]
    halted: bool  # 수집 중단 여부
    quarantine_records: list[dict[str, Any]]  # 격리 테이블 저장용


class DataQualityPipeline:
    """3단계 데이터 품질 파이프라인.

    사용 예:
        pipeline = DataQualityPipeline(config)
        result = pipeline.process_ohlcv(data)
        if result.accepted:
            await db.insert(result.data)
        for qr in result.quarantine_records:
            await db.insert_quarantine(qr)
    """

    # 이상치 탐지 대상 필드 (OHLCV)
    PRICE_FIELDS = ("open", "high", "low", "close")
    VOLUME_FIELDS = ("volume",)

    def __init__(self, config: DataQualityConfig) -> None:
        self._config = config
        self.detector = AnomalyDetector(config)
        self.healer = DataHealer(config)
        self.validator = DataValidator(config)

    def process_ohlcv(
        self,
        data: dict[str, Any],
    ) -> PipelineResult:
        """OHLCV 데이터를 3단계 파이프라인으로 처리한다.

        Args:
            data: OHLCV 데이터 dict (symbol, timestamp, open, high, low, close, volume)

        Returns:
            PipelineResult: 처리 결과
        """
        symbol = data.get("symbol", "")
        anomalies: list[AnomalyResult] = []
        healings: list[HealingResult] = []
        quarantine_records: list[dict[str, Any]] = []

        # Step 0: 수집 중단 확인
        if self.validator.should_halt(symbol):
            return PipelineResult(
                accepted=False,
                data=data,
                validation=None,
                anomalies=[],
                healings=[],
                halted=True,
                quarantine_records=[],
            )

        # Step 1: 기본 검증
        validation = self.validator.validate_ohlcv(data)
        if not validation.valid:
            return PipelineResult(
                accepted=False,
                data=data,
                validation=validation,
                anomalies=[],
                healings=[],
                halted=False,
                quarantine_records=[],
            )

        # Step 2: 가격/볼륨 이상치 탐지 + 힐링
        processed_data = dict(data)  # 사본
        all_fields = list(self.PRICE_FIELDS) + list(self.VOLUME_FIELDS)

        for field_name in all_fields:
            value = processed_data.get(field_name)
            if value is None:
                continue

            result = self.detector.detect(symbol, field_name, float(value))
            anomalies.append(result)

            is_anomaly = result.is_anomaly
            self.validator.record_anomaly_check(symbol, is_anomaly)

            if is_anomaly:
                # 격리 레코드 생성
                quarantine_records.append({
                    "symbol": symbol,
                    "field_name": field_name,
                    "raw_value": value,
                    "anomaly_method": result.method,
                    "anomaly_score": result.score,
                    "threshold_exceeded": result.threshold,
                })

                if self._config.quarantine_enabled:
                    # 힐링 시도
                    healing = self.healer.heal(symbol, field_name, float(value))
                    healings.append(healing)

                    if healing.success:
                        processed_data[field_name] = healing.healed_value
                        processed_data.setdefault("_healing_flags", {})[field_name] = {
                            "method": healing.method,
                            "original": healing.original_value,
                        }
                    else:
                        # 힐링 실패 시 데이터 거부
                        return PipelineResult(
                            accepted=False,
                            data=data,
                            validation=validation,
                            anomalies=anomalies,
                            healings=healings,
                            halted=False,
                            quarantine_records=quarantine_records,
                        )
            else:
                # 정상 값은 힐러 히스토리에 기록
                self.healer.record_valid(symbol, field_name, float(value))

        # Step 3: 수집 중단 재확인
        halted = self.validator.should_halt(symbol)
        if halted:
            return PipelineResult(
                accepted=False,
                data=processed_data,
                validation=validation,
                anomalies=anomalies,
                healings=healings,
                halted=True,
                quarantine_records=quarantine_records,
            )

        return PipelineResult(
            accepted=True,
            data=processed_data,
            validation=validation,
            anomalies=anomalies,
            healings=healings,
            halted=False,
            quarantine_records=quarantine_records,
        )

    def process_ticker(self, data: dict[str, Any]) -> PipelineResult:
        """실시간 틱 데이터를 검증한다."""
        symbol = data.get("symbol", "")

        if self.validator.should_halt(symbol):
            return PipelineResult(
                accepted=False, data=data, validation=None,
                anomalies=[], healings=[], halted=True, quarantine_records=[],
            )

        validation = self.validator.validate_ticker(data)
        if not validation.valid:
            return PipelineResult(
                accepted=False, data=data, validation=validation,
                anomalies=[], healings=[], halted=False, quarantine_records=[],
            )

        price = data.get("price")
        if price is not None:
            result = self.detector.detect(symbol, "price", float(price))
            self.validator.record_anomaly_check(symbol, result.is_anomaly)

            if result.is_anomaly:
                healing = self.healer.heal(symbol, "price", float(price))
                if healing.success:
                    data = dict(data)
                    data["price"] = healing.healed_value
                    data["_healed"] = True
                else:
                    return PipelineResult(
                        accepted=False, data=data, validation=validation,
                        anomalies=[result], healings=[healing],
                        halted=False, quarantine_records=[],
                    )
            else:
                self.healer.record_valid(symbol, "price", float(price))

        return PipelineResult(
            accepted=True, data=data, validation=validation,
            anomalies=[], healings=[], halted=False, quarantine_records=[],
        )

    def resume(self, symbol: str) -> None:
        """중단된 심볼의 수집을 재개한다."""
        self.validator.resume_collection(symbol)
        self.detector.reset(symbol)
        self.healer.reset(symbol)
