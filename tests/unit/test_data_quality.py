"""데이터 품질 파이프라인 단위 테스트 (P10)."""

from __future__ import annotations

import time

import pytest

from src.core.config import DataQualityConfig
from src.data.quality.anomaly_detector import AnomalyDetector, AnomalyResult
from src.data.quality.data_healer import DataHealer, HealingResult
from src.data.quality.validator import DataValidator, ValidationResult


@pytest.fixture
def dq_config() -> DataQualityConfig:
    return DataQualityConfig()


class TestAnomalyDetector:
    def test_insufficient_data_not_anomaly(self, dq_config) -> None:
        detector = AnomalyDetector(dq_config)
        # 10개 미만에서는 이상치 판정 불가 → False
        result = detector.detect("BTC", "close", 50_000.0)
        assert result.is_anomaly is False
        assert result.method == "insufficient_data"

    def test_normal_value_after_window(self, dq_config) -> None:
        detector = AnomalyDetector(dq_config)
        # 윈도우 채우기 (10개 이상)
        for i in range(30):
            detector.detect("BTC", "close", 50_000.0 + i * 10)
        # 정상 범위 값
        result = detector.detect("BTC", "close", 50_200.0)
        assert result.is_anomaly is False

    def test_extreme_value_detected(self, dq_config) -> None:
        detector = AnomalyDetector(dq_config)
        for i in range(30):
            detector.detect("BTC", "close", 50_000.0 + i * 10)
        # 극단적 값 (50000~50300 범위에서 100000은 극단적)
        result = detector.detect("BTC", "close", 100_000.0)
        assert result.is_anomaly is True
        assert isinstance(result, AnomalyResult)

    def test_window_stats(self, dq_config) -> None:
        detector = AnomalyDetector(dq_config)
        for i in range(10):
            detector.detect("ETH", "volume", 1000.0 + i)
        stats = detector.get_window_stats("ETH", "volume")
        assert stats["count"] == 10

    def test_reset(self, dq_config) -> None:
        detector = AnomalyDetector(dq_config)
        for i in range(10):
            detector.detect("BTC", "close", 50_000.0)
        detector.reset("BTC")
        stats = detector.get_window_stats("BTC", "close")
        assert stats["count"] == 0


class TestDataHealer:
    def test_linear_interpolation(self, dq_config) -> None:
        healer = DataHealer(dq_config)
        healer.record_valid("BTC", "close", 100.0)
        result = healer.heal("BTC", "close", 999.0, next_value=110.0)
        assert isinstance(result, HealingResult)
        assert result.success is True
        assert result.healed_value == pytest.approx(105.0)
        assert result.method == "linear_interpolation"

    def test_forward_fill(self, dq_config) -> None:
        healer = DataHealer(dq_config)
        healer.record_valid("BTC", "close", 100.0)
        # next_value 없으면 forward fill로 폴백
        result = healer.heal("BTC", "close", 999.0)
        assert result.success is True
        assert result.healed_value == 100.0

    def test_moving_average(self) -> None:
        dq_config_ma = DataQualityConfig(healing_method="moving_average")
        healer = DataHealer(dq_config_ma)
        for v in [100.0, 110.0, 105.0]:
            healer.record_valid("BTC", "close", v)
        result = healer.heal("BTC", "close", 999.0)
        assert result.success is True
        assert result.healed_value == pytest.approx(105.0)

    def test_no_history_fails(self, dq_config) -> None:
        healer = DataHealer(dq_config)
        result = healer.heal("NEW", "close", 999.0)
        assert result.success is False


class TestDataValidator:
    def test_valid_ohlcv(self, dq_config) -> None:
        validator = DataValidator(dq_config)
        result = validator.validate_ohlcv({
            "symbol": "BTC/KRW",
            "timestamp": time.time(),
            "open": 100.0,
            "high": 105.0,
            "low": 98.0,
            "close": 103.0,
            "volume": 1000.0,
        })
        assert result.valid is True
        assert len(result.errors) == 0

    def test_null_field(self, dq_config) -> None:
        validator = DataValidator(dq_config)
        result = validator.validate_ohlcv({
            "symbol": "BTC/KRW",
            "timestamp": time.time(),
            "open": None,
            "high": 105.0,
            "low": 98.0,
            "close": 103.0,
            "volume": 1000.0,
        })
        assert result.valid is False
        assert len(result.errors) > 0

    def test_negative_price(self, dq_config) -> None:
        validator = DataValidator(dq_config)
        result = validator.validate_ohlcv({
            "symbol": "BTC/KRW",
            "timestamp": time.time(),
            "open": -1.0,
            "high": 105.0,
            "low": 98.0,
            "close": 103.0,
            "volume": 1000.0,
        })
        assert result.valid is False

    def test_high_less_than_low(self, dq_config) -> None:
        validator = DataValidator(dq_config)
        result = validator.validate_ohlcv({
            "symbol": "BTC/KRW",
            "timestamp": time.time(),
            "open": 100.0,
            "high": 95.0,
            "low": 98.0,
            "close": 97.0,
            "volume": 1000.0,
        })
        assert result.valid is False

    def test_negative_volume(self, dq_config) -> None:
        validator = DataValidator(dq_config)
        result = validator.validate_ohlcv({
            "symbol": "BTC/KRW",
            "timestamp": time.time(),
            "open": 100.0,
            "high": 105.0,
            "low": 98.0,
            "close": 103.0,
            "volume": -1.0,
        })
        assert result.valid is False

    def test_should_halt_high_anomaly_ratio(self, dq_config) -> None:
        validator = DataValidator(dq_config)
        # 높은 이상치 비율 기록
        for _ in range(20):
            validator.record_anomaly_check("BTC", is_anomaly=True)
        for _ in range(5):
            validator.record_anomaly_check("BTC", is_anomaly=False)
        assert validator.should_halt("BTC") is True

    def test_should_not_halt_low_anomaly_ratio(self, dq_config) -> None:
        validator = DataValidator(dq_config)
        for _ in range(2):
            validator.record_anomaly_check("BTC", is_anomaly=True)
        for _ in range(100):
            validator.record_anomaly_check("BTC", is_anomaly=False)
        assert validator.should_halt("BTC") is False

    def test_resume_collection(self, dq_config) -> None:
        validator = DataValidator(dq_config)
        for _ in range(30):
            validator.record_anomaly_check("BTC", is_anomaly=True)
        validator.should_halt("BTC")  # halt 발동
        validator.resume_collection("BTC")
        assert "BTC" not in validator.halted_symbols
