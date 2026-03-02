"""데이터 삽입 전 검증 모듈 (ARCHITECTURE.md P10 Stage 3).

수집된 시장 데이터의 기본 무결성을 검증한다.
- NOT NULL, 타임스탬프 연속, 가격 > 0, 볼륨 ≥ 0
- 이상치 비율 임계치 초과 시 수집 자동 중단
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from src.core.config import DataQualityConfig

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """검증 결과."""

    valid: bool
    errors: list[str]
    data: dict[str, Any]


class DataValidator:
    """시장 데이터 삽입 전 검증기.

    OHLCV + 기본 필드 검증 및 심볼별 이상치 비율 모니터링.
    """

    def __init__(self, config: DataQualityConfig) -> None:
        self._config = config
        # symbol → deque of (timestamp, is_anomaly)
        self._anomaly_tracker: dict[str, deque[tuple[float, bool]]] = {}
        # symbol → halted flag
        self._halted_symbols: set[str] = set()

    def validate_ohlcv(self, data: dict[str, Any]) -> ValidationResult:
        """OHLCV 캔들 데이터를 검증한다.

        필수 필드: symbol, timestamp, open, high, low, close, volume
        """
        errors: list[str] = []

        # NOT NULL 검사
        required = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
        for field in required:
            if data.get(field) is None:
                errors.append(f"Required field '{field}' is null")

        if errors:
            return ValidationResult(valid=False, errors=errors, data=data)

        # 가격 > 0 검사
        for price_field in ("open", "high", "low", "close"):
            val = data.get(price_field, 0)
            if not isinstance(val, (int, float)) or val <= 0:
                errors.append(f"Price '{price_field}' must be > 0, got {val}")

        # 볼륨 ≥ 0 검사
        volume = data.get("volume", -1)
        if not isinstance(volume, (int, float)) or volume < 0:
            errors.append(f"Volume must be >= 0, got {volume}")

        # OHLC 정합성: high ≥ max(open, close), low ≤ min(open, close)
        o, h, l, c = (
            data.get("open", 0),
            data.get("high", 0),
            data.get("low", 0),
            data.get("close", 0),
        )
        if isinstance(h, (int, float)) and isinstance(l, (int, float)):
            if h < l:
                errors.append(f"High ({h}) < Low ({l})")
            if isinstance(o, (int, float)) and isinstance(c, (int, float)):
                if h < max(o, c):
                    errors.append(f"High ({h}) < max(Open, Close) ({max(o, c)})")
                if l > min(o, c):
                    errors.append(f"Low ({l}) > min(Open, Close) ({min(o, c)})")

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            data=data,
        )

    def validate_ticker(self, data: dict[str, Any]) -> ValidationResult:
        """실시간 틱 데이터를 검증한다."""
        errors: list[str] = []

        if data.get("symbol") is None:
            errors.append("Required field 'symbol' is null")
        if data.get("price") is None:
            errors.append("Required field 'price' is null")
        elif not isinstance(data["price"], (int, float)) or data["price"] <= 0:
            errors.append(f"Price must be > 0, got {data['price']}")

        return ValidationResult(valid=len(errors) == 0, errors=errors, data=data)

    # ── 이상치 비율 모니터링 ──

    def record_anomaly_check(
        self, symbol: str, is_anomaly: bool
    ) -> None:
        """이상치 탐지 결과를 기록한다."""
        if symbol not in self._anomaly_tracker:
            self._anomaly_tracker[symbol] = deque(
                maxlen=1000,  # 최대 1000건 추적
            )
        self._anomaly_tracker[symbol].append((time.time(), is_anomaly))

    def should_halt(self, symbol: str) -> bool:
        """이상치 비율이 임계치를 초과하여 수집을 중단해야 하는지 확인한다."""
        if symbol in self._halted_symbols:
            return True

        tracker = self._anomaly_tracker.get(symbol)
        if not tracker:
            return False

        window_seconds = self._config.anomaly_halt_window_minutes * 60
        now = time.time()
        cutoff = now - window_seconds

        # 윈도우 내 기록만 필터링
        recent = [(ts, anom) for ts, anom in tracker if ts >= cutoff]
        if len(recent) < 10:
            return False

        anomaly_count = sum(1 for _, a in recent if a)
        ratio = anomaly_count / len(recent)

        if ratio > self._config.anomaly_halt_ratio:
            self._halted_symbols.add(symbol)
            logger.error(
                "Data collection HALTED for %s: anomaly ratio %.1f%% > %.1f%% "
                "(window=%dm, samples=%d)",
                symbol,
                ratio * 100,
                self._config.anomaly_halt_ratio * 100,
                self._config.anomaly_halt_window_minutes,
                len(recent),
            )
            return True

        return False

    def resume_collection(self, symbol: str) -> None:
        """중단된 심볼의 수집을 재개한다."""
        self._halted_symbols.discard(symbol)
        # 이상치 트래커 초기화
        if symbol in self._anomaly_tracker:
            self._anomaly_tracker[symbol].clear()
        logger.info("Data collection RESUMED for %s", symbol)

    def get_anomaly_stats(self, symbol: str) -> dict[str, Any]:
        """심볼별 이상치 통계를 반환한다."""
        tracker = self._anomaly_tracker.get(symbol)
        if not tracker:
            return {"symbol": symbol, "total": 0, "anomalies": 0, "ratio": 0.0}

        window_seconds = self._config.anomaly_halt_window_minutes * 60
        cutoff = time.time() - window_seconds
        recent = [(ts, anom) for ts, anom in tracker if ts >= cutoff]
        anomaly_count = sum(1 for _, a in recent if a)

        return {
            "symbol": symbol,
            "total": len(recent),
            "anomalies": anomaly_count,
            "ratio": anomaly_count / len(recent) if recent else 0.0,
            "halted": symbol in self._halted_symbols,
        }

    @property
    def halted_symbols(self) -> set[str]:
        return self._halted_symbols.copy()
