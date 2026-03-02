"""ShadowTester 단위 테스트."""

from __future__ import annotations

import pytest

from src.agents.quant.shadow_tester import DailySnapshot, ShadowTester
from src.agents.quant.strategies.registry import (
    StrategyEntry,
    StrategyRegistry,
    StrategyStatus,
)
from src.core.config import ShadowTestConfig


@pytest.fixture
def registry() -> StrategyRegistry:
    reg = StrategyRegistry()
    reg.register(StrategyEntry(name="shadow_s1", status=StrategyStatus.SHADOW))
    reg.register(StrategyEntry(name="live_s1", status=StrategyStatus.LIVE))
    return reg


@pytest.fixture
def shadow_tester(registry: StrategyRegistry) -> ShadowTester:
    return ShadowTester(
        registry=registry,
        config=ShadowTestConfig(
            promotion_sharpe_min=1.0,
            promotion_days_min=3,
            promotion_win_rate_min=0.50,
            demotion_win_rate_max=0.40,
            demotion_mdd_max=0.25,
            demotion_consecutive_days=2,
        ),
    )


class TestShadowTester:
    def test_start_shadow_valid(self, shadow_tester: ShadowTester) -> None:
        assert shadow_tester.start_shadow("shadow_s1")
        assert "shadow_s1" in shadow_tester.active_sessions

    def test_start_shadow_non_shadow_status(self, shadow_tester: ShadowTester) -> None:
        assert not shadow_tester.start_shadow("live_s1")
        assert "live_s1" not in shadow_tester.active_sessions

    def test_start_shadow_nonexistent(self, shadow_tester: ShadowTester) -> None:
        assert not shadow_tester.start_shadow("nonexistent")

    def test_start_shadow_duplicate(self, shadow_tester: ShadowTester) -> None:
        shadow_tester.start_shadow("shadow_s1")
        assert shadow_tester.start_shadow("shadow_s1")  # Idempotent

    def test_stop_shadow(self, shadow_tester: ShadowTester) -> None:
        shadow_tester.start_shadow("shadow_s1")
        assert shadow_tester.stop_shadow("shadow_s1")
        assert "shadow_s1" not in shadow_tester.active_sessions
        assert not shadow_tester.stop_shadow("shadow_s1")  # Already stopped

    def test_feed_signal(self, shadow_tester: ShadowTester) -> None:
        shadow_tester.start_shadow("shadow_s1")
        result = shadow_tester.feed_signal("shadow_s1", {
            "symbol": "BTC/USDT",
            "direction": "BUY",
            "entry_price": 50000,
            "position_size_usd": 10000,
        })
        assert result is not None
        assert result.get("executed")

    def test_feed_signal_no_session(self, shadow_tester: ShadowTester) -> None:
        result = shadow_tester.feed_signal("nonexistent", {})
        assert result is None

    def test_estimate_sharpe(self) -> None:
        snapshots = [
            DailySnapshot(date=f"2025-01-{i:02d}", win_rate=0.6,
                         total_pnl_pct=i * 0.01, sharpe_estimate=0, max_drawdown_pct=0, total_trades=5)
            for i in range(1, 8)
        ]
        sharpe = ShadowTester._estimate_sharpe(snapshots, 0.08)
        assert isinstance(sharpe, float)

    def test_estimate_sharpe_insufficient(self) -> None:
        sharpe = ShadowTester._estimate_sharpe([], 0.01)
        assert sharpe == 0.0

    def test_estimate_mdd(self) -> None:
        snapshots = [
            DailySnapshot(date="d1", win_rate=0, total_pnl_pct=0.05,
                         sharpe_estimate=0, max_drawdown_pct=0, total_trades=0),
            DailySnapshot(date="d2", win_rate=0, total_pnl_pct=0.10,
                         sharpe_estimate=0, max_drawdown_pct=0, total_trades=0),
            DailySnapshot(date="d3", win_rate=0, total_pnl_pct=0.03,
                         sharpe_estimate=0, max_drawdown_pct=0, total_trades=0),
        ]
        mdd = ShadowTester._estimate_mdd(snapshots, 0.03)
        assert mdd == pytest.approx(0.07, abs=0.001)

    def test_get_session_status(self, shadow_tester: ShadowTester) -> None:
        shadow_tester.start_shadow("shadow_s1")
        status = shadow_tester.get_session_status()
        assert len(status) == 1
        assert status[0]["strategy_name"] == "shadow_s1"
        assert "performance" in status[0]
