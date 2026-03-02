"""Walk-Forward Optimizer 단위 테스트."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.agents.quant.strategies.builtin import create_mean_reversion_strategy
from src.agents.quant.walk_forward_optimizer import (
    WFOConfig,
    WalkForwardOptimizer,
)
from src.core.event_engine import Bar


def _generate_bars(n: int = 500, symbol: str = "BTC/USDT") -> list[Bar]:
    """합성 OHLCV 데이터 생성 (사인파 기반)."""
    import math

    bars: list[Bar] = []
    base_price = 50000.0
    for i in range(n):
        # 사인파 + 미세 노이즈
        price = base_price + 5000 * math.sin(i / 50) + (i % 7) * 10
        high = price * 1.005
        low = price * 0.995
        volume = 100 + (i % 20) * 10
        bars.append(Bar(
            symbol=symbol,
            timestamp=datetime(2025, 1, 1, i % 24, 0, tzinfo=timezone.utc),
            open=price,
            high=high,
            low=low,
            close=price,
            volume=volume,
        ))
    return bars


class TestWFOConfig:
    def test_defaults(self) -> None:
        cfg = WFOConfig()
        assert cfg.in_sample_bars == 1000
        assert cfg.out_sample_bars == 336
        assert cfg.step_bars == 168
        assert cfg.min_oos_ratio == 0.60


class TestWalkForwardOptimizer:
    def test_generate_windows_basic(self) -> None:
        optimizer = WalkForwardOptimizer(WFOConfig(
            in_sample_bars=100, out_sample_bars=50, step_bars=50,
        ))
        windows = optimizer._generate_windows(300)
        assert len(windows) >= 2
        for is_start, is_end, oos_start, oos_end in windows:
            assert is_end - is_start == 100
            assert oos_start == is_end
            assert oos_end - oos_start <= 50

    def test_generate_windows_insufficient_data(self) -> None:
        optimizer = WalkForwardOptimizer(WFOConfig(
            in_sample_bars=100, out_sample_bars=50,
        ))
        windows = optimizer._generate_windows(100)  # 부족
        assert len(windows) == 0

    def test_compute_objective(self) -> None:
        from src.core.event_engine import BacktestResult

        result = BacktestResult(sharpe_ratio=2.0, max_drawdown_pct=10.0)
        score = WalkForwardOptimizer._compute_objective(result)
        # 2.0 - 0.5 * (10/100) = 2.0 - 0.05 = 1.95
        assert abs(score - 1.95) < 0.01

    def test_count_combinations(self) -> None:
        grid = {"a": [1, 2, 3], "b": [10, 20]}
        assert WalkForwardOptimizer._count_combinations(grid) == 6

    @pytest.mark.asyncio
    async def test_optimize_small_grid(self) -> None:
        """소규모 그리드로 WFO 실행."""
        bars = _generate_bars(400)
        optimizer = WalkForwardOptimizer(WFOConfig(
            in_sample_bars=150,
            out_sample_bars=80,
            step_bars=80,
            initial_balance=10_000.0,
        ))

        param_grid = {
            "rsi_oversold": [25, 30],
            "rsi_overbought": [70, 75],
        }

        summary = await optimizer.optimize(
            "mean_reversion",
            create_mean_reversion_strategy,
            param_grid,
            bars,
        )

        assert summary.strategy_name == "mean_reversion"
        assert len(summary.windows) >= 1
        assert isinstance(summary.best_params, dict)
        assert isinstance(summary.avg_oos_score, float)
        assert 0.0 <= summary.overfit_ratio <= 1.0

    @pytest.mark.asyncio
    async def test_optimize_insufficient_data(self) -> None:
        """데이터 부족 시 빈 결과 반환."""
        bars = _generate_bars(50)
        optimizer = WalkForwardOptimizer(WFOConfig(
            in_sample_bars=1000, out_sample_bars=336,
        ))

        summary = await optimizer.optimize(
            "test", create_mean_reversion_strategy, {"rsi_oversold": [30]}, bars,
        )
        assert len(summary.windows) == 0
        assert summary.best_params == {}
