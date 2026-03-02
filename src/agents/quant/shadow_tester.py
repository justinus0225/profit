"""그림자 테스팅 — 실시간 데이터에서 전략 성과 가상 평가.

SHADOW 상태 전략을 실시간 데이터 스트림에 대해
ForwardTester로 가상 체결하면서 성과를 측정하고,
승격(LIVE)/강등(DEPRECATED) 판정을 수행한다.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.agents.qa.forward_test import ForwardTester
from src.agents.quant.strategies.registry import StrategyRegistry, StrategyStatus
from src.core.config import ShadowTestConfig

logger = logging.getLogger(__name__)


@dataclass
class DailySnapshot:
    """일일 성과 스냅샷."""

    date: str
    win_rate: float
    total_pnl_pct: float
    sharpe_estimate: float
    max_drawdown_pct: float
    total_trades: int


@dataclass
class ShadowSession:
    """단일 전략의 그림자 테스트 세션."""

    strategy_name: str
    tester: ForwardTester
    start_date: datetime
    daily_snapshots: list[DailySnapshot] = field(default_factory=list)
    consecutive_promotion_days: int = 0
    consecutive_demotion_days: int = 0
    last_eval_date: str = ""


class ShadowTester:
    """그림자 테스트 매니저.

    StrategyRegistry에서 SHADOW 상태 전략을 추적하고,
    ForwardTester로 실시간 가격에 대한 가상 체결을 수행한 뒤
    일일 평가를 통해 승격/강등을 판정한다.
    """

    def __init__(
        self,
        registry: StrategyRegistry,
        config: ShadowTestConfig | None = None,
    ) -> None:
        self._registry = registry
        self._config = config or ShadowTestConfig()
        self._sessions: dict[str, ShadowSession] = {}

    def start_shadow(
        self,
        strategy_name: str,
        initial_balance: float = 100_000.0,
    ) -> bool:
        """SHADOW 전략에 대한 테스트 세션 시작."""
        entry = self._registry.get(strategy_name)
        if not entry or entry.status != StrategyStatus.SHADOW:
            logger.warning(
                "Cannot start shadow for %s (not found or not SHADOW)", strategy_name
            )
            return False

        if strategy_name in self._sessions:
            logger.info("Shadow session already active: %s", strategy_name)
            return True

        self._sessions[strategy_name] = ShadowSession(
            strategy_name=strategy_name,
            tester=ForwardTester(initial_balance=initial_balance),
            start_date=datetime.now(tz=timezone.utc),
        )
        logger.info("Shadow session started: %s", strategy_name)
        return True

    def stop_shadow(self, strategy_name: str) -> bool:
        """세션 종료."""
        removed = self._sessions.pop(strategy_name, None)
        if removed:
            logger.info("Shadow session stopped: %s", strategy_name)
        return removed is not None

    def feed_signal(
        self, strategy_name: str, signal: dict[str, Any]
    ) -> dict[str, Any] | None:
        """SHADOW 전략에 가상 신호 전달."""
        session = self._sessions.get(strategy_name)
        if not session:
            return None
        return session.tester.receive_signal(signal)

    def check_stops(self, prices: dict[str, float]) -> None:
        """모든 활성 세션에 가격 전파 → 손절/목표가 체크."""
        for session in self._sessions.values():
            session.tester.check_stops(prices)

    def evaluate_daily(self) -> list[dict[str, Any]]:
        """일일 평가 — 승격/강등 판정.

        Returns:
            전이 이벤트 목록: [{"strategy_name", "action": "promote"|"demote", ...}]
        """
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        transitions: list[dict[str, Any]] = []

        for name, session in list(self._sessions.items()):
            if session.last_eval_date == today:
                continue  # 오늘 이미 평가함

            perf = session.tester.get_performance()
            win_rate = perf.get("win_rate", 0.0)
            total_pnl_pct = perf.get("total_pnl_pct", 0.0)
            total_trades = perf.get("total_trades", 0)

            # Sharpe 추정 (일일 수익률 기반)
            sharpe_est = self._estimate_sharpe(session.daily_snapshots, total_pnl_pct)

            # MDD 추정
            mdd_pct = self._estimate_mdd(session.daily_snapshots, total_pnl_pct)

            snapshot = DailySnapshot(
                date=today,
                win_rate=win_rate,
                total_pnl_pct=total_pnl_pct,
                sharpe_estimate=sharpe_est,
                max_drawdown_pct=mdd_pct,
                total_trades=total_trades,
            )
            session.daily_snapshots.append(snapshot)
            session.last_eval_date = today

            # 승격 조건 체크
            cfg = self._config
            if (
                sharpe_est >= cfg.promotion_sharpe_min
                and win_rate >= cfg.promotion_win_rate_min
                and total_trades >= 3  # 최소 거래 수
            ):
                session.consecutive_promotion_days += 1
                session.consecutive_demotion_days = 0
            else:
                session.consecutive_promotion_days = 0

            # 강등 조건 체크
            if win_rate < cfg.demotion_win_rate_max or mdd_pct > cfg.demotion_mdd_max:
                session.consecutive_demotion_days += 1
            else:
                session.consecutive_demotion_days = 0

            # 승격 판정
            if session.consecutive_promotion_days >= cfg.promotion_days_min:
                transitions.append({
                    "strategy_name": name,
                    "action": "promote",
                    "metrics": {
                        "sharpe": sharpe_est,
                        "win_rate": win_rate,
                        "promotion_days": session.consecutive_promotion_days,
                    },
                })
                logger.info(
                    "Shadow %s: PROMOTE (sharpe=%.2f wr=%.2f%% days=%d)",
                    name, sharpe_est, win_rate * 100,
                    session.consecutive_promotion_days,
                )

            # 강등 판정
            elif session.consecutive_demotion_days >= cfg.demotion_consecutive_days:
                transitions.append({
                    "strategy_name": name,
                    "action": "demote",
                    "metrics": {
                        "win_rate": win_rate,
                        "mdd": mdd_pct,
                        "demotion_days": session.consecutive_demotion_days,
                    },
                })
                logger.info(
                    "Shadow %s: DEMOTE (wr=%.2f%% mdd=%.2f%% days=%d)",
                    name, win_rate * 100, mdd_pct * 100,
                    session.consecutive_demotion_days,
                )

            # 레지스트리 메트릭 업데이트
            self._registry.update_metrics(name, {
                "win_rate": win_rate,
                "total_pnl_pct": total_pnl_pct,
                "sharpe_estimate": sharpe_est,
                "max_drawdown_pct": mdd_pct,
                "shadow_days": len(session.daily_snapshots),
            })

        return transitions

    @staticmethod
    def _estimate_sharpe(
        snapshots: list[DailySnapshot], current_pnl_pct: float
    ) -> float:
        """일별 PnL 기반 Sharpe Ratio 추정 (연율화)."""
        if len(snapshots) < 2:
            return 0.0

        daily_returns: list[float] = []
        prev_pnl = 0.0
        for s in snapshots:
            daily_returns.append(s.total_pnl_pct - prev_pnl)
            prev_pnl = s.total_pnl_pct
        # 현재까지
        daily_returns.append(current_pnl_pct - prev_pnl)

        if not daily_returns:
            return 0.0

        avg = sum(daily_returns) / len(daily_returns)
        var = sum((r - avg) ** 2 for r in daily_returns) / len(daily_returns)
        std = math.sqrt(var) if var > 0 else 1e-10
        return (avg / std) * math.sqrt(365)

    @staticmethod
    def _estimate_mdd(
        snapshots: list[DailySnapshot], current_pnl_pct: float
    ) -> float:
        """일별 PnL 기반 최대 낙폭 추정."""
        pnls = [s.total_pnl_pct for s in snapshots] + [current_pnl_pct]
        if not pnls:
            return 0.0

        peak = pnls[0]
        mdd = 0.0
        for pnl in pnls:
            if pnl > peak:
                peak = pnl
            dd = peak - pnl
            if dd > mdd:
                mdd = dd
        return mdd

    @property
    def active_sessions(self) -> list[str]:
        """활성 세션 전략 이름 목록."""
        return list(self._sessions.keys())

    def get_session_status(self) -> list[dict[str, Any]]:
        """전체 세션 상태 요약."""
        result: list[dict[str, Any]] = []
        for name, session in self._sessions.items():
            perf = session.tester.get_performance()
            result.append({
                "strategy_name": name,
                "start_date": session.start_date.isoformat(),
                "days": len(session.daily_snapshots),
                "promotion_days": session.consecutive_promotion_days,
                "demotion_days": session.consecutive_demotion_days,
                "performance": perf,
            })
        return result
