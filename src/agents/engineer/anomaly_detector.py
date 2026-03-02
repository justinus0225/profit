"""이상치 탐지 (Z-Score + IQR) (P10).

src.data.quality.anomaly_detector의 구현을 에이전트 계층에서 재노출한다.
"""

from src.data.quality.anomaly_detector import AnomalyDetector, AnomalyResult

__all__ = ["AnomalyDetector", "AnomalyResult"]
