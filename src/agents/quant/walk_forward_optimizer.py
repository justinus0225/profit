"""Walk-Forward 파라미터 최적화.

In-Sample/Out-of-Sample 윈도우를 롤링하면서
과적합(overfitting)을 방지하며 전략 파라미터를 최적화한다.

기존 BacktestEngine + SimulatedBroker를 재활용하여
파라미터 그리드 탐색을 수행한다.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from src.core.event_engine import (
    BacktestEngine,
    BacktestResult,
    Bar,
    EventDispatcher,
    EventType,
    HistoricalDataFeed,
    SimulatedBroker,
)

logger = logging.getLogger(__name__)


@dataclass
class WFOConfig:
    """Walk-Forward Optimizer 설정."""

    in_sample_bars: int = 1000     # IS 윈도우 크기 (~42일, 1h 기준)
    out_sample_bars: int = 336     # OOS 윈도우 크기 (~14일)
    step_bars: int = 168           # 롤링 스텝 (~7일)
    min_oos_ratio: float = 0.60    # OOS/IS score 최소 비율 (과적합 판정)
    initial_balance: float = 100_000.0
    commission_rate: float = 0.001


@dataclass
class WFOResult:
    """단일 윈도우 최적화 결과."""

    window_index: int
    best_params: dict[str, Any]
    in_sample_score: float
    out_sample_score: float
    oos_ratio: float
    is_overfit: bool
    backtest_result: BacktestResult


@dataclass
class WFOSummary:
    """전체 WFO 실행 요약."""

    strategy_name: str
    windows: list[WFOResult] = field(default_factory=list)
    best_params: dict[str, Any] = field(default_factory=dict)
    avg_oos_score: float = 0.0
    overfit_ratio: float = 0.0  # 과적합 윈도우 비율

    @property
    def is_robust(self) -> bool:
        """과적합 비율 50% 미만이면 강건한 파라미터로 판정."""
        return self.overfit_ratio < 0.50


class WalkForwardOptimizer:
    """Walk-Forward 파라미터 최적화.

    BacktestEngine을 재활용하여 In-Sample 그리드 서치 후
    Out-of-Sample에서 검증한다.
    """

    def __init__(self, config: WFOConfig | None = None) -> None:
        self._config = config or WFOConfig()

    async def optimize(
        self,
        strategy_name: str,
        strategy_factory: Callable[..., Any],
        param_grid: dict[str, list[Any]],
        bars: list[Bar],
    ) -> WFOSummary:
        """Walk-Forward 최적화를 실행한다.

        Args:
            strategy_name: 전략 이름
            strategy_factory: (broker, **params) -> on_bar 핸들러
            param_grid: {"param_name": [val1, val2, ...]}
            bars: 전체 OHLCV Bar 리스트 (시간순)

        Returns:
            WFOSummary: 윈도우별 결과 + 최적 파라미터
        """
        windows = self._generate_windows(len(bars))
        if not windows:
            logger.warning(
                "[WFO] Not enough bars (%d) for optimization", len(bars)
            )
            return WFOSummary(strategy_name=strategy_name)

        logger.info(
            "[WFO] %s: %d windows, %d param combinations",
            strategy_name, len(windows),
            self._count_combinations(param_grid),
        )

        results: list[WFOResult] = []
        for i, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
            is_bars = bars[is_start:is_end]
            oos_bars = bars[oos_start:oos_end]

            # In-Sample 그리드 서치
            best_params, is_score = await self._grid_search(
                strategy_factory, param_grid, is_bars,
            )

            # Out-of-Sample 검증
            oos_result = await self._run_backtest(
                strategy_factory, best_params, oos_bars,
            )
            oos_score = self._compute_objective(oos_result)

            # 과적합 판정
            oos_ratio = oos_score / is_score if is_score > 0 else 0.0
            is_overfit = oos_ratio < self._config.min_oos_ratio

            result = WFOResult(
                window_index=i,
                best_params=best_params,
                in_sample_score=is_score,
                out_sample_score=oos_score,
                oos_ratio=round(oos_ratio, 3),
                is_overfit=is_overfit,
                backtest_result=oos_result,
            )
            results.append(result)

            logger.info(
                "[WFO] Window %d: IS=%.3f OOS=%.3f ratio=%.3f %s params=%s",
                i, is_score, oos_score, oos_ratio,
                "OVERFIT" if is_overfit else "OK",
                best_params,
            )

        # 최적 파라미터 선정: 과적합 아닌 윈도우 중 OOS 점수 최고
        valid_results = [r for r in results if not r.is_overfit]
        if valid_results:
            best_window = max(valid_results, key=lambda r: r.out_sample_score)
            best_params = best_window.best_params
        elif results:
            best_window = max(results, key=lambda r: r.out_sample_score)
            best_params = best_window.best_params
        else:
            best_params = {}

        overfit_count = sum(1 for r in results if r.is_overfit)
        avg_oos = sum(r.out_sample_score for r in results) / len(results) if results else 0.0

        summary = WFOSummary(
            strategy_name=strategy_name,
            windows=results,
            best_params=best_params,
            avg_oos_score=round(avg_oos, 3),
            overfit_ratio=round(overfit_count / len(results), 3) if results else 1.0,
        )

        logger.info(
            "[WFO] %s complete: best_params=%s avg_oos=%.3f overfit_ratio=%.1f%% robust=%s",
            strategy_name, best_params, avg_oos,
            summary.overfit_ratio * 100, summary.is_robust,
        )
        return summary

    def _generate_windows(
        self, total_bars: int
    ) -> list[tuple[int, int, int, int]]:
        """롤링 윈도우 생성.

        Returns:
            [(is_start, is_end, oos_start, oos_end), ...]
        """
        cfg = self._config
        min_required = cfg.in_sample_bars + cfg.out_sample_bars
        if total_bars < min_required:
            return []

        windows: list[tuple[int, int, int, int]] = []
        start = 0
        while start + min_required <= total_bars:
            is_start = start
            is_end = start + cfg.in_sample_bars
            oos_start = is_end
            oos_end = min(oos_start + cfg.out_sample_bars, total_bars)
            windows.append((is_start, is_end, oos_start, oos_end))
            start += cfg.step_bars

        return windows

    async def _grid_search(
        self,
        factory: Callable[..., Any],
        param_grid: dict[str, list[Any]],
        bars: list[Bar],
    ) -> tuple[dict[str, Any], float]:
        """In-Sample 그리드 탐색: 모든 파라미터 조합에 대해 백테스트."""
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        combinations = list(itertools.product(*param_values))

        best_params: dict[str, Any] = {}
        best_score = float("-inf")

        for combo in combinations:
            params = dict(zip(param_names, combo))
            result = await self._run_backtest(factory, params, bars)
            score = self._compute_objective(result)

            if score > best_score:
                best_score = score
                best_params = params

        return best_params, best_score

    async def _run_backtest(
        self,
        factory: Callable[..., Any],
        params: dict[str, Any],
        bars: list[Bar],
    ) -> BacktestResult:
        """단일 파라미터 세트로 백테스트 실행."""
        feed = HistoricalDataFeed(bars)
        broker = SimulatedBroker(
            initial_balance=self._config.initial_balance,
            commission_rate=self._config.commission_rate,
        )
        dispatcher = EventDispatcher()

        strategy_fn = factory(broker=broker, **params)
        dispatcher.on(EventType.BAR, strategy_fn)

        engine = BacktestEngine(feed, broker, dispatcher)
        return await engine.run()

    @staticmethod
    def _compute_objective(result: BacktestResult) -> float:
        """최적화 목적 함수: Sharpe - 0.5 * MDD (%).

        높을수록 좋은 전략.
        """
        return result.sharpe_ratio - 0.5 * (result.max_drawdown_pct / 100)

    @staticmethod
    def _count_combinations(param_grid: dict[str, list[Any]]) -> int:
        """파라미터 조합 수 계산."""
        count = 1
        for values in param_grid.values():
            count *= len(values)
        return count
