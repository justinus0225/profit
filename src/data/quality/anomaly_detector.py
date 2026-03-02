"""이상치 탐지 모듈 (ARCHITECTURE.md P10 Stage 1).

Z-Score 및 IQR 기반 이상치 탐지.
슬라이딩 윈도우로 통계량을 산출하고, OR 조건으로 이상치를 판정한다.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from src.core.config import DataQualityConfig

logger = logging.getLogger(__name__)


@dataclass
class AnomalyResult:
    """이상치 탐지 결과."""

    is_anomaly: bool
    value: float
    method: str  # "zscore" | "iqr" | "both"
    score: float  # Z-Score 또는 IQR 초과량
    threshold: float
    window_stats: dict[str, float] = field(default_factory=dict)


class AnomalyDetector:
    """슬라이딩 윈도우 기반 이상치 탐지기.

    심볼+필드별로 독립적인 윈도우를 유지한다.
    """

    def __init__(self, config: DataQualityConfig) -> None:
        self._config = config
        # (symbol, field) → deque of recent values
        self._windows: dict[tuple[str, str], deque[float]] = {}

    def _get_window(self, symbol: str, field_name: str) -> deque[float]:
        key = (symbol, field_name)
        if key not in self._windows:
            self._windows[key] = deque(maxlen=self._config.window_size)
        return self._windows[key]

    def detect(
        self,
        symbol: str,
        field_name: str,
        value: float,
    ) -> AnomalyResult:
        """값의 이상치 여부를 판정한다.

        Args:
            symbol: 코인 심볼 (예: "BTC/USDT")
            field_name: 필드 이름 (예: "close", "volume")
            value: 검사할 값

        Returns:
            AnomalyResult: 탐지 결과
        """
        window = self._get_window(symbol, field_name)

        # 윈도우가 충분하지 않으면 정상 처리
        if len(window) < 10:
            window.append(value)
            return AnomalyResult(
                is_anomaly=False,
                value=value,
                method="insufficient_data",
                score=0.0,
                threshold=0.0,
            )

        zscore_result = self._zscore_detect(window, value)
        iqr_result = self._iqr_detect(window, value)

        # OR 조건: 어느 하나라도 이상치면 이상치 판정
        is_anomaly = zscore_result.is_anomaly or iqr_result.is_anomaly

        if zscore_result.is_anomaly and iqr_result.is_anomaly:
            method = "both"
        elif zscore_result.is_anomaly:
            method = "zscore"
        elif iqr_result.is_anomaly:
            method = "iqr"
        else:
            method = "none"

        # 이상치가 아니면 윈도우에 추가
        if not is_anomaly:
            window.append(value)

        score = max(abs(zscore_result.score), abs(iqr_result.score))
        threshold = (
            zscore_result.threshold
            if abs(zscore_result.score) >= abs(iqr_result.score)
            else iqr_result.threshold
        )

        stats = {
            "zscore": zscore_result.score,
            "zscore_threshold": self._config.zscore_threshold,
            "iqr_score": iqr_result.score,
            "iqr_multiplier": self._config.iqr_multiplier,
            "window_size": len(window),
        }

        if is_anomaly:
            logger.warning(
                "Anomaly detected: %s/%s value=%.6f method=%s score=%.2f",
                symbol,
                field_name,
                value,
                method,
                score,
            )

        return AnomalyResult(
            is_anomaly=is_anomaly,
            value=value,
            method=method,
            score=score,
            threshold=threshold,
            window_stats=stats,
        )

    def _zscore_detect(self, window: deque[float], value: float) -> AnomalyResult:
        """Z-Score 기반 이상치 탐지."""
        values = list(window)
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        std = math.sqrt(variance) if variance > 0 else 0.0

        if std == 0:
            zscore = 0.0
        else:
            zscore = (value - mean) / std

        threshold = self._config.zscore_threshold
        return AnomalyResult(
            is_anomaly=abs(zscore) > threshold,
            value=value,
            method="zscore",
            score=zscore,
            threshold=threshold,
        )

    def _iqr_detect(self, window: deque[float], value: float) -> AnomalyResult:
        """IQR (사분위 범위) 기반 이상치 탐지."""
        values = sorted(window)
        n = len(values)

        q1 = values[n // 4]
        q3 = values[(3 * n) // 4]
        iqr = q3 - q1

        multiplier = self._config.iqr_multiplier
        lower = q1 - multiplier * iqr
        upper = q3 + multiplier * iqr

        if value < lower:
            score = (lower - value) / iqr if iqr > 0 else 0.0
            is_anomaly = True
        elif value > upper:
            score = (value - upper) / iqr if iqr > 0 else 0.0
            is_anomaly = True
        else:
            score = 0.0
            is_anomaly = False

        return AnomalyResult(
            is_anomaly=is_anomaly,
            value=value,
            method="iqr",
            score=score,
            threshold=multiplier,
        )

    def get_window_stats(self, symbol: str, field_name: str) -> dict[str, Any]:
        """윈도우 통계를 반환한다."""
        window = self._get_window(symbol, field_name)
        if not window:
            return {"count": 0}
        values = list(window)
        return {
            "count": len(values),
            "mean": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
        }

    def reset(self, symbol: str | None = None) -> None:
        """윈도우를 초기화한다."""
        if symbol is None:
            self._windows.clear()
        else:
            keys_to_remove = [k for k in self._windows if k[0] == symbol]
            for k in keys_to_remove:
                del self._windows[k]
