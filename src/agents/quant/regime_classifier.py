"""시장 국면 분류기.

기존 IndicatorEngine의 ADX, ATR을 활용한 규칙 기반 분류.
신규 의존성 없이 시장 상태를 3가지 국면으로 분류한다.

향후 Phase C-2에서 HMM 기반 분류기 추가 가능 (optional dep).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class MarketRegime(str, Enum):
    """시장 국면."""

    TRENDING = "trending"    # 추세장: ADX 높음
    RANGING = "ranging"      # 횡보장: ADX 낮음 + 변동성 보통
    VOLATILE = "volatile"    # 변동성 확대: ATR percentile 높음


@dataclass
class RegimeClassification:
    """국면 분류 결과."""

    regime: MarketRegime
    confidence: float  # 0.0 ~ 1.0
    adx: float | None = None
    atr_percentile: float | None = None
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )


# 국면별 전략 가중치 조정 배율
REGIME_STRATEGY_WEIGHTS: dict[MarketRegime, dict[str, float]] = {
    MarketRegime.TRENDING: {
        "trend_following": 1.5,
        "momentum": 1.2,
        "mean_reversion": 0.5,
        "breakout": 1.3,
        "combined": 1.0,
    },
    MarketRegime.RANGING: {
        "trend_following": 0.5,
        "momentum": 0.8,
        "mean_reversion": 1.5,
        "breakout": 0.7,
        "combined": 1.0,
    },
    MarketRegime.VOLATILE: {
        "trend_following": 0.8,
        "momentum": 0.6,
        "mean_reversion": 0.8,
        "breakout": 1.4,
        "combined": 1.0,
    },
}


class RuleBasedRegimeClassifier:
    """규칙 기반 시장 국면 분류기.

    IndicatorEngine이 이미 계산한 ADX와 ATR을 활용한다.
    - ADX ≥ threshold → TRENDING
    - ATR percentile ≥ threshold → VOLATILE
    - 나머지 → RANGING
    """

    def __init__(
        self,
        adx_trend_threshold: float = 25.0,
        atr_volatile_percentile: float = 75.0,
        lookback: int = 100,
    ) -> None:
        self._adx_threshold = adx_trend_threshold
        self._atr_percentile_threshold = atr_volatile_percentile
        self._lookback = lookback
        self._atr_history: list[float] = []
        self._last_classification: RegimeClassification | None = None

    def classify(self, indicators: dict[str, Any]) -> RegimeClassification:
        """지표 데이터로 국면을 분류한다.

        Args:
            indicators: IndicatorEngine.compute() 결과
                        (adx, atr 등 포함)

        Returns:
            RegimeClassification
        """
        adx = indicators.get("adx")
        atr = indicators.get("atr")

        # ATR 히스토리 업데이트
        if atr is not None:
            self._atr_history.append(atr)
            if len(self._atr_history) > self._lookback:
                self._atr_history = self._atr_history[-self._lookback:]

        atr_pct = self._atr_percentile_rank(atr) if atr is not None else 50.0

        # 분류 로직
        if adx is not None and adx >= self._adx_threshold:
            regime = MarketRegime.TRENDING
            confidence = min(1.0, adx / 50.0)
        elif atr_pct >= self._atr_percentile_threshold:
            regime = MarketRegime.VOLATILE
            confidence = min(1.0, atr_pct / 100.0)
        else:
            regime = MarketRegime.RANGING
            confidence = 0.7  # 소거법이므로 고정 신뢰도

        classification = RegimeClassification(
            regime=regime,
            confidence=round(confidence, 3),
            adx=round(adx, 2) if adx is not None else None,
            atr_percentile=round(atr_pct, 1),
        )
        self._last_classification = classification
        return classification

    def _atr_percentile_rank(self, current_atr: float | None) -> float:
        """현재 ATR의 히스토리 내 백분위 순위."""
        if current_atr is None or not self._atr_history:
            return 50.0
        below = sum(1 for h in self._atr_history if h <= current_atr)
        return (below / len(self._atr_history)) * 100

    def get_strategy_weights(
        self, classification: RegimeClassification | None = None
    ) -> dict[str, float]:
        """현재 국면에 적합한 전략 가중치 반환."""
        cls = classification or self._last_classification
        if cls is None:
            return {s: 1.0 for s in REGIME_STRATEGY_WEIGHTS[MarketRegime.RANGING]}
        return dict(REGIME_STRATEGY_WEIGHTS.get(cls.regime, {}))

    @property
    def current_regime(self) -> MarketRegime | None:
        """마지막 분류 결과의 국면."""
        if self._last_classification is None:
            return None
        return self._last_classification.regime

    @property
    def last_classification(self) -> RegimeClassification | None:
        return self._last_classification

    def to_dict(self) -> dict[str, Any]:
        """직렬화 가능한 상태 반환."""
        cls = self._last_classification
        return {
            "regime": cls.regime.value if cls else None,
            "confidence": cls.confidence if cls else 0.0,
            "adx": cls.adx if cls else None,
            "atr_percentile": cls.atr_percentile if cls else None,
            "strategy_weights": self.get_strategy_weights(),
            "atr_history_len": len(self._atr_history),
        }
