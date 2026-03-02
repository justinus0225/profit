"""RuleBasedRegimeClassifier 단위 테스트."""

from __future__ import annotations

import pytest

from src.agents.quant.regime_classifier import (
    REGIME_STRATEGY_WEIGHTS,
    MarketRegime,
    RuleBasedRegimeClassifier,
)


@pytest.fixture
def classifier() -> RuleBasedRegimeClassifier:
    return RuleBasedRegimeClassifier(
        adx_trend_threshold=25.0,
        atr_volatile_percentile=75.0,
        lookback=20,
    )


class TestRuleBasedRegimeClassifier:
    def test_trending_regime(self, classifier: RuleBasedRegimeClassifier) -> None:
        """ADX >= 25 → TRENDING."""
        result = classifier.classify({"adx": 35.0, "atr": 500})
        assert result.regime == MarketRegime.TRENDING
        assert result.confidence > 0.0
        assert result.adx == 35.0

    def test_trending_high_adx(self, classifier: RuleBasedRegimeClassifier) -> None:
        """ADX 50 → confidence 1.0."""
        result = classifier.classify({"adx": 50.0, "atr": 500})
        assert result.regime == MarketRegime.TRENDING
        assert result.confidence == 1.0

    def test_volatile_regime(self, classifier: RuleBasedRegimeClassifier) -> None:
        """ADX 낮고 ATR percentile 높음 → VOLATILE."""
        # ATR 히스토리 채우기 (낮은 값들)
        for val in range(20):
            classifier.classify({"adx": 10.0, "atr": 100 + val})

        # 극단적으로 높은 ATR
        result = classifier.classify({"adx": 15.0, "atr": 1000})
        assert result.regime == MarketRegime.VOLATILE
        assert result.atr_percentile is not None
        assert result.atr_percentile >= 75.0

    def test_ranging_regime(self, classifier: RuleBasedRegimeClassifier) -> None:
        """ADX 낮고 ATR 보통 → RANGING."""
        # ATR 히스토리 채우기
        for val in range(20):
            classifier.classify({"adx": 15.0, "atr": 500})

        result = classifier.classify({"adx": 15.0, "atr": 500})
        assert result.regime == MarketRegime.RANGING
        assert result.confidence == 0.7

    def test_none_indicators(self, classifier: RuleBasedRegimeClassifier) -> None:
        """지표가 None일 때 기본 RANGING."""
        result = classifier.classify({"adx": None, "atr": None})
        assert result.regime == MarketRegime.RANGING

    def test_strategy_weights(self, classifier: RuleBasedRegimeClassifier) -> None:
        """국면별 가중치 반환."""
        classifier.classify({"adx": 35.0, "atr": 500})
        weights = classifier.get_strategy_weights()
        assert weights["trend_following"] == 1.5
        assert weights["mean_reversion"] == 0.5

    def test_strategy_weights_no_classification(self, classifier: RuleBasedRegimeClassifier) -> None:
        """분류 전에는 기본 가중치(1.0) 반환."""
        weights = classifier.get_strategy_weights()
        for w in weights.values():
            assert w == 1.0

    def test_current_regime(self, classifier: RuleBasedRegimeClassifier) -> None:
        assert classifier.current_regime is None
        classifier.classify({"adx": 35.0, "atr": 500})
        assert classifier.current_regime == MarketRegime.TRENDING

    def test_to_dict(self, classifier: RuleBasedRegimeClassifier) -> None:
        classifier.classify({"adx": 35.0, "atr": 500})
        d = classifier.to_dict()
        assert d["regime"] == "trending"
        assert "confidence" in d
        assert "strategy_weights" in d

    def test_atr_history_limited(self, classifier: RuleBasedRegimeClassifier) -> None:
        """ATR 히스토리가 lookback 이하로 유지됨."""
        for i in range(50):
            classifier.classify({"adx": 10.0, "atr": 100 + i})
        assert len(classifier._atr_history) <= 20


class TestRegimeStrategyWeights:
    def test_all_regimes_have_weights(self) -> None:
        for regime in MarketRegime:
            assert regime in REGIME_STRATEGY_WEIGHTS
            weights = REGIME_STRATEGY_WEIGHTS[regime]
            assert "trend_following" in weights
            assert "mean_reversion" in weights
            assert "momentum" in weights
            assert "breakout" in weights

    def test_trending_favors_trend_following(self) -> None:
        weights = REGIME_STRATEGY_WEIGHTS[MarketRegime.TRENDING]
        assert weights["trend_following"] > weights["mean_reversion"]

    def test_ranging_favors_mean_reversion(self) -> None:
        weights = REGIME_STRATEGY_WEIGHTS[MarketRegime.RANGING]
        assert weights["mean_reversion"] > weights["trend_following"]

    def test_volatile_favors_breakout(self) -> None:
        weights = REGIME_STRATEGY_WEIGHTS[MarketRegime.VOLATILE]
        assert weights["breakout"] > weights["momentum"]
