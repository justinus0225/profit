"""데이터 힐링 모듈 (ARCHITECTURE.md P10 Stage 2).

이상치로 판정된 데이터를 복구한다.
힐링 방법 (우선순위순):
1. 선형 보간 (Linear Interpolation) - 전후 값이 있을 때
2. Forward Fill (이전 값 유지) - 이후 값이 없을 때
3. 이동 평균 대체 (MA Replacement) - 이전 값이 없을 때
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass

from src.core.config import DataQualityConfig

logger = logging.getLogger(__name__)


@dataclass
class HealingResult:
    """힐링 결과."""

    original_value: float
    healed_value: float
    method: str  # "linear_interpolation" | "forward_fill" | "moving_average"
    success: bool


class DataHealer:
    """이상치 데이터 힐링 엔진."""

    def __init__(self, config: DataQualityConfig) -> None:
        self._config = config
        # (symbol, field) → deque of recent valid values
        self._history: dict[tuple[str, str], deque[float]] = {}

    def _get_history(self, symbol: str, field_name: str) -> deque[float]:
        key = (symbol, field_name)
        if key not in self._history:
            self._history[key] = deque(maxlen=self._config.window_size)
        return self._history[key]

    def record_valid(self, symbol: str, field_name: str, value: float) -> None:
        """정상 값을 히스토리에 기록한다."""
        self._get_history(symbol, field_name).append(value)

    def heal(
        self,
        symbol: str,
        field_name: str,
        anomaly_value: float,
        *,
        next_value: float | None = None,
    ) -> HealingResult:
        """이상치 값을 힐링한다.

        Args:
            symbol: 코인 심볼
            field_name: 필드 이름
            anomaly_value: 이상치 원본 값
            next_value: 다음 정상 값 (선형 보간용, 실시간에서는 없을 수 있음)

        Returns:
            HealingResult: 힐링 결과
        """
        method = self._config.healing_method
        history = self._get_history(symbol, field_name)

        if method == "linear_interpolation" and history and next_value is not None:
            return self._linear_interpolation(anomaly_value, history[-1], next_value)

        if method == "linear_interpolation" and history:
            # 다음 값이 없으면 forward fill로 폴백
            return self._forward_fill(anomaly_value, history)

        if method == "forward_fill" and history:
            return self._forward_fill(anomaly_value, history)

        if method == "moving_average" and len(history) >= 3:
            return self._moving_average(anomaly_value, history)

        # 자동 폴백 순서: forward_fill → moving_average → 실패
        if history:
            return self._forward_fill(anomaly_value, history)
        if len(history) >= 3:
            return self._moving_average(anomaly_value, history)

        logger.warning(
            "Healing failed (no history): %s/%s value=%.6f",
            symbol,
            field_name,
            anomaly_value,
        )
        return HealingResult(
            original_value=anomaly_value,
            healed_value=anomaly_value,
            method="none",
            success=False,
        )

    def _linear_interpolation(
        self, anomaly_value: float, prev_value: float, next_value: float
    ) -> HealingResult:
        """선형 보간: (prev + next) / 2."""
        healed = (prev_value + next_value) / 2
        return HealingResult(
            original_value=anomaly_value,
            healed_value=healed,
            method="linear_interpolation",
            success=True,
        )

    def _forward_fill(
        self, anomaly_value: float, history: deque[float]
    ) -> HealingResult:
        """이전 값 유지."""
        healed = history[-1]
        return HealingResult(
            original_value=anomaly_value,
            healed_value=healed,
            method="forward_fill",
            success=True,
        )

    def _moving_average(
        self, anomaly_value: float, history: deque[float]
    ) -> HealingResult:
        """최근 이동 평균으로 대체."""
        recent = list(history)[-min(20, len(history)) :]
        healed = sum(recent) / len(recent)
        return HealingResult(
            original_value=anomaly_value,
            healed_value=healed,
            method="moving_average",
            success=True,
        )

    def reset(self, symbol: str | None = None) -> None:
        """히스토리를 초기화한다."""
        if symbol is None:
            self._history.clear()
        else:
            keys_to_remove = [k for k in self._history if k[0] == symbol]
            for k in keys_to_remove:
                del self._history[k]
