"""에이전트 성과 추적 단위 테스트 (P4)."""

from __future__ import annotations

import pytest

from src.tracking.agent_performance import (
    DEFAULT_WEIGHT,
    MAX_WEIGHT,
    MIN_WEIGHT,
    AgentPerformanceTracker,
    AgentScorecard,
)


class TestAgentScorecard:
    def test_default_values(self) -> None:
        sc = AgentScorecard(agent_type="quant")
        assert sc.total_decisions == 0
        assert sc.ema_accuracy == 0.5
        assert sc.consensus_weight == DEFAULT_WEIGHT

    def test_accuracy_calculation(self) -> None:
        sc = AgentScorecard(
            agent_type="quant", total_decisions=10, correct_decisions=7
        )
        assert sc.accuracy == 0.7


class TestAgentPerformanceTracker:
    @pytest.mark.asyncio
    async def test_record_outcome_correct(self, fake_redis) -> None:
        tracker = AgentPerformanceTracker(fake_redis)
        sc = await tracker.record_outcome("quant", correct=True)
        assert sc.total_decisions == 1
        assert sc.correct_decisions == 1

    @pytest.mark.asyncio
    async def test_record_outcome_incorrect(self, fake_redis) -> None:
        tracker = AgentPerformanceTracker(fake_redis)
        sc = await tracker.record_outcome("analyst", correct=False)
        assert sc.total_decisions == 1
        assert sc.correct_decisions == 0

    @pytest.mark.asyncio
    async def test_ema_updates(self, fake_redis) -> None:
        tracker = AgentPerformanceTracker(fake_redis)
        # 연속 정답 → EMA 증가
        for _ in range(5):
            sc = await tracker.record_outcome("quant", correct=True)
        assert sc.ema_accuracy > 0.5

    @pytest.mark.asyncio
    async def test_weight_default_before_min_samples(
        self, fake_redis
    ) -> None:
        tracker = AgentPerformanceTracker(fake_redis)
        # 10개 미만에서는 기본 가중치
        for _ in range(5):
            await tracker.record_outcome("quant", correct=True)
        assert tracker.get_weight("quant") == DEFAULT_WEIGHT

    @pytest.mark.asyncio
    async def test_weight_adjusts_after_min_samples(
        self, fake_redis
    ) -> None:
        tracker = AgentPerformanceTracker(fake_redis)
        # 10개 이상 → 가중치 조정
        for _ in range(15):
            await tracker.record_outcome("quant", correct=True)
        weight = tracker.get_weight("quant")
        assert MIN_WEIGHT <= weight <= MAX_WEIGHT

    @pytest.mark.asyncio
    async def test_get_scorecard(self, fake_redis) -> None:
        tracker = AgentPerformanceTracker(fake_redis)
        assert tracker.get_scorecard("unknown") is None

        await tracker.record_outcome("risk", correct=True)
        sc = tracker.get_scorecard("risk")
        assert sc is not None
        assert sc.agent_type == "risk"

    @pytest.mark.asyncio
    async def test_get_all_scorecards(self, fake_redis) -> None:
        tracker = AgentPerformanceTracker(fake_redis)
        await tracker.record_outcome("quant", correct=True)
        await tracker.record_outcome("analyst", correct=False)

        all_sc = tracker.get_all_scorecards()
        assert "quant" in all_sc
        assert "analyst" in all_sc

    @pytest.mark.asyncio
    async def test_save_and_load(self, fake_redis) -> None:
        tracker = AgentPerformanceTracker(fake_redis)
        await tracker.record_outcome("quant", correct=True)
        await tracker.record_outcome("quant", correct=True)
        await tracker.save("quant")

        # 새 인스턴스로 로드
        tracker2 = AgentPerformanceTracker(fake_redis)
        await tracker2.load()
        sc = tracker2.get_scorecard("quant")
        assert sc is not None
        assert sc.total_decisions == 2
