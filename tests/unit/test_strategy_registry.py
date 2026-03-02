"""StrategyRegistry 단위 테스트."""

from __future__ import annotations

import pytest

from src.agents.quant.strategies.registry import (
    StrategyEntry,
    StrategyRegistry,
    StrategyStatus,
)


@pytest.fixture
def registry() -> StrategyRegistry:
    return StrategyRegistry(max_strategies=5)


@pytest.fixture
def sample_entry() -> StrategyEntry:
    return StrategyEntry(
        name="test_strategy",
        status=StrategyStatus.CANDIDATE,
        parameters={"rsi_oversold": 30},
        source="builtin",
    )


class TestStrategyRegistry:
    def test_register_and_get(self, registry: StrategyRegistry, sample_entry: StrategyEntry) -> None:
        assert registry.register(sample_entry)
        assert registry.count == 1

        retrieved = registry.get("test_strategy")
        assert retrieved is not None
        assert retrieved.name == "test_strategy"
        assert retrieved.parameters["rsi_oversold"] == 30

    def test_get_nonexistent(self, registry: StrategyRegistry) -> None:
        assert registry.get("nonexistent") is None

    def test_max_strategies_limit(self, registry: StrategyRegistry) -> None:
        for i in range(5):
            entry = StrategyEntry(name=f"strategy_{i}")
            assert registry.register(entry)

        # 6번째는 실패
        overflow = StrategyEntry(name="overflow")
        assert not registry.register(overflow)
        assert registry.count == 5

    def test_update_existing_bypasses_limit(self, registry: StrategyRegistry) -> None:
        for i in range(5):
            entry = StrategyEntry(name=f"strategy_{i}")
            registry.register(entry)

        # 기존 전략 업데이트는 성공
        updated = StrategyEntry(name="strategy_0", parameters={"new": True})
        assert registry.register(updated)
        assert registry.get("strategy_0").parameters["new"] is True

    def test_get_by_status(self, registry: StrategyRegistry) -> None:
        registry.register(StrategyEntry(name="live1", status=StrategyStatus.LIVE))
        registry.register(StrategyEntry(name="live2", status=StrategyStatus.LIVE))
        registry.register(StrategyEntry(name="shadow1", status=StrategyStatus.SHADOW))

        live = registry.get_by_status(StrategyStatus.LIVE)
        assert len(live) == 2

        shadow = registry.get_by_status(StrategyStatus.SHADOW)
        assert len(shadow) == 1

        deprecated = registry.get_by_status(StrategyStatus.DEPRECATED)
        assert len(deprecated) == 0

    def test_valid_transitions(self, registry: StrategyRegistry) -> None:
        registry.register(StrategyEntry(name="s1", status=StrategyStatus.CANDIDATE))

        # CANDIDATE → SHADOW
        assert registry.transition("s1", StrategyStatus.SHADOW)
        assert registry.get("s1").status == StrategyStatus.SHADOW

        # SHADOW → LIVE
        assert registry.transition("s1", StrategyStatus.LIVE)
        assert registry.get("s1").status == StrategyStatus.LIVE

        # LIVE → DEPRECATED
        assert registry.transition("s1", StrategyStatus.DEPRECATED)
        assert registry.get("s1").status == StrategyStatus.DEPRECATED

        # DEPRECATED → CANDIDATE (재활성화)
        assert registry.transition("s1", StrategyStatus.CANDIDATE)
        assert registry.get("s1").status == StrategyStatus.CANDIDATE

    def test_invalid_transitions(self, registry: StrategyRegistry) -> None:
        registry.register(StrategyEntry(name="s1", status=StrategyStatus.CANDIDATE))

        # CANDIDATE → LIVE (불가)
        assert not registry.transition("s1", StrategyStatus.LIVE)
        assert registry.get("s1").status == StrategyStatus.CANDIDATE

    def test_transition_nonexistent(self, registry: StrategyRegistry) -> None:
        assert not registry.transition("nonexistent", StrategyStatus.LIVE)

    def test_shadow_transition_sets_shadow_start(self, registry: StrategyRegistry) -> None:
        registry.register(StrategyEntry(name="s1", status=StrategyStatus.CANDIDATE))
        assert registry.get("s1").shadow_start is None

        registry.transition("s1", StrategyStatus.SHADOW)
        assert registry.get("s1").shadow_start is not None
        assert registry.get("s1").shadow_days_passed == 0

    def test_update_params(self, registry: StrategyRegistry) -> None:
        registry.register(StrategyEntry(name="s1", parameters={"a": 1}))
        assert registry.update_params("s1", {"b": 2})
        assert registry.get("s1").parameters == {"a": 1, "b": 2}

    def test_update_metrics(self, registry: StrategyRegistry) -> None:
        registry.register(StrategyEntry(name="s1"))
        assert registry.update_metrics("s1", {"win_rate": 0.6, "sharpe": 1.5})
        assert registry.get("s1").metrics["win_rate"] == 0.6

    def test_remove(self, registry: StrategyRegistry) -> None:
        registry.register(StrategyEntry(name="s1"))
        assert registry.count == 1
        assert registry.remove("s1")
        assert registry.count == 0
        assert not registry.remove("s1")  # 이미 삭제됨

    def test_list_all(self, registry: StrategyRegistry) -> None:
        registry.register(StrategyEntry(name="a"))
        registry.register(StrategyEntry(name="b"))
        assert len(registry.list_all()) == 2

    def test_to_summary(self, registry: StrategyRegistry) -> None:
        registry.register(StrategyEntry(name="s1", status=StrategyStatus.LIVE, source="builtin"))
        summary = registry.to_summary()
        assert len(summary) == 1
        assert summary[0]["name"] == "s1"
        assert summary[0]["status"] == "live"
        assert summary[0]["source"] == "builtin"
