"""데이터 품질 모니터링 모듈.

이상치 비율, 힐링 성공률, 심볼별 데이터 신뢰도 등
품질 메트릭을 수집하고 리포팅한다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class QualityMonitor:
    """데이터 품질 메트릭 수집 및 리포팅."""

    def __init__(self) -> None:
        self._anomaly_counts: dict[str, int] = {}
        self._total_checks: dict[str, int] = {}
        self._healing_success: int = 0
        self._healing_failure: int = 0

    def record_anomaly(self, symbol: str, is_anomaly: bool) -> None:
        """이상치 탐지 결과를 기록한다."""
        self._total_checks[symbol] = self._total_checks.get(symbol, 0) + 1
        if is_anomaly:
            self._anomaly_counts[symbol] = self._anomaly_counts.get(symbol, 0) + 1

    def record_healing(self, success: bool) -> None:
        """힐링 시도 결과를 기록한다."""
        if success:
            self._healing_success += 1
        else:
            self._healing_failure += 1

    def get_anomaly_rate(self, symbol: str) -> float:
        """심볼별 이상치 비율을 반환한다."""
        total = self._total_checks.get(symbol, 0)
        if total == 0:
            return 0.0
        return self._anomaly_counts.get(symbol, 0) / total

    def get_report(self) -> dict[str, Any]:
        """품질 리포트를 생성한다."""
        total_anomalies = sum(self._anomaly_counts.values())
        total_checks = sum(self._total_checks.values())
        total_healings = self._healing_success + self._healing_failure

        return {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "total_checks": total_checks,
            "total_anomalies": total_anomalies,
            "anomaly_rate": total_anomalies / total_checks if total_checks > 0 else 0.0,
            "healing_success": self._healing_success,
            "healing_failure": self._healing_failure,
            "healing_rate": (
                self._healing_success / total_healings if total_healings > 0 else 0.0
            ),
            "symbol_anomaly_rates": {
                sym: self.get_anomaly_rate(sym)
                for sym in sorted(self._total_checks)
            },
        }

    def reset(self) -> None:
        """메트릭을 초기화한다."""
        self._anomaly_counts.clear()
        self._total_checks.clear()
        self._healing_success = 0
        self._healing_failure = 0
