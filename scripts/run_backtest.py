#!/usr/bin/env python3
"""Stage 1 백테스트 실행기.

과거 OHLCV CSV 데이터를 로드하여 Unified Event Engine으로
전략 성과를 시뮬레이션한다. LLM API 키 불필요.

Usage:
    # 1) 먼저 데이터 다운로드 (네트워크 필요, 1회만)
    python scripts/download_ohlcv.py --symbol BTC/USDT --timeframe 1h --days 180

    # 2) 백테스트 실행 (오프라인 가능)
    python scripts/run_backtest.py --data data/ohlcv/BTC_USDT_1h.csv
    python scripts/run_backtest.py --data data/ohlcv/BTC_USDT_1h.csv --balance 10000 --strategy trend_following

Stage Gate 기준 (ARCHITECTURE.md 13.3.3):
    Sharpe Ratio > 1.0
    MDD < -20%
    Win Rate > 50%
    Profit Factor > 1.5
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.event_engine import (
    BacktestEngine,
    BacktestResult,
    Bar,
    Broker,
    Event,
    EventDispatcher,
    EventType,
    Fill,
    HistoricalDataFeed,
    OrderRequest,
    SimulatedBroker,
)


# ── CSV → Bar 로더 ──

def load_csv(filepath: Path, symbol: str, timeframe: str) -> list[Bar]:
    """CSV 파일에서 Bar 리스트를 로드한다."""
    bars: list[Bar] = []
    with open(filepath, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            bars.append(Bar(
                symbol=symbol,
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                timeframe=timeframe,
            ))
    return bars


# ── 규칙 기반 전략 (LLM 불필요) ──

class RuleBasedStrategy:
    """규칙 기반 매매 전략.

    pandas-ta 없이 순수 Python으로 지표를 계산한다.
    백테스트에서 LLM 의존성을 제거하기 위해 사용.
    """

    def __init__(
        self,
        broker: SimulatedBroker,
        strategy_type: str = "mean_reversion",
        position_size_pct: float = 0.20,  # 가용 자금의 20%
    ) -> None:
        self._broker = broker
        self._strategy_type = strategy_type
        self._position_pct = position_size_pct
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._volumes: list[float] = []
        self._current_symbol = ""
        self._in_position = False

    async def on_bar(self, event: Event) -> None:
        """BAR 이벤트를 받아 전략 로직을 실행한다."""
        d = event.data
        self._current_symbol = d["symbol"]
        self._closes.append(d["close"])
        self._highs.append(d["high"])
        self._lows.append(d["low"])
        self._volumes.append(d["volume"])

        # 최소 50개 캔들 필요
        if len(self._closes) < 50:
            return

        if self._strategy_type == "mean_reversion":
            await self._mean_reversion(d)
        elif self._strategy_type == "trend_following":
            await self._trend_following(d)
        elif self._strategy_type == "momentum":
            await self._momentum(d)
        elif self._strategy_type == "breakout":
            await self._breakout(d)
        elif self._strategy_type == "combined":
            await self._combined(d)

    # ── 전략 구현 ──

    async def _mean_reversion(self, d: dict[str, Any]) -> None:
        """Mean Reversion: RSI 과매도 매수, 과매수 매도."""
        rsi = self._calc_rsi(14)
        if rsi is None:
            return

        if not self._in_position and rsi <= 30:
            await self._buy(d["close"])
        elif self._in_position and rsi >= 70:
            await self._sell(d["close"])

    async def _trend_following(self, d: dict[str, Any]) -> None:
        """Trend Following: MA(20) > MA(50) 골든크로스 매수."""
        ma20 = self._calc_sma(20)
        ma50 = self._calc_sma(50)
        if ma20 is None or ma50 is None:
            return

        if not self._in_position and ma20 > ma50 and self._closes[-2] <= self._calc_sma_at(50, -2, 20):
            await self._buy(d["close"])
        elif self._in_position and ma20 < ma50:
            await self._sell(d["close"])

    async def _momentum(self, d: dict[str, Any]) -> None:
        """Momentum: ROC(12) 양전환 매수."""
        roc = self._calc_roc(12)
        if roc is None:
            return

        if not self._in_position and roc > 3.0:
            await self._buy(d["close"])
        elif self._in_position and roc < -2.0:
            await self._sell(d["close"])

    async def _breakout(self, d: dict[str, Any]) -> None:
        """Breakout: 20-bar 고점 돌파 매수, ATR 기반 손절."""
        if len(self._highs) < 21:
            return

        high_20 = max(self._highs[-21:-1])
        low_20 = min(self._lows[-21:-1])
        atr = self._calc_atr(14)

        if not self._in_position and d["close"] > high_20:
            await self._buy(d["close"])
        elif self._in_position and atr is not None:
            # ATR x 2 손절
            positions = await self._broker.get_positions()
            if self._current_symbol in positions:
                entry = self._broker._avg_prices.get(self._current_symbol, d["close"])
                if d["close"] < entry - atr * 2:
                    await self._sell(d["close"])

    async def _combined(self, d: dict[str, Any]) -> None:
        """Combined: RSI + MA + Volume 복합."""
        rsi = self._calc_rsi(14)
        ma20 = self._calc_sma(20)
        ma50 = self._calc_sma(50)
        vol_ratio = self._calc_volume_ratio(20)

        if rsi is None or ma20 is None or ma50 is None:
            return

        buy_signals = 0
        if rsi < 40:
            buy_signals += 1
        if ma20 > ma50:
            buy_signals += 1
        if vol_ratio is not None and vol_ratio > 1.5:
            buy_signals += 1

        if not self._in_position and buy_signals >= 2:
            await self._buy(d["close"])
        elif self._in_position and (rsi > 75 or ma20 < ma50):
            await self._sell(d["close"])

    # ── 주문 실행 ──

    async def _buy(self, price: float) -> None:
        """매수 주문."""
        balance = await self._broker.get_balance()
        invest = balance * self._position_pct
        qty = invest / price
        if qty <= 0:
            return
        try:
            await self._broker.submit_order(OrderRequest(
                symbol=self._current_symbol,
                side="buy",
                order_type="market",
                quantity=qty,
                price=price,
            ))
            self._in_position = True
        except ValueError:
            pass

    async def _sell(self, price: float) -> None:
        """전량 매도."""
        positions = await self._broker.get_positions()
        qty = positions.get(self._current_symbol, 0)
        if qty <= 0:
            self._in_position = False
            return
        await self._broker.submit_order(OrderRequest(
            symbol=self._current_symbol,
            side="sell",
            order_type="market",
            quantity=qty,
            price=price,
        ))
        self._in_position = False

    # ── 지표 계산 (순수 Python) ──

    def _calc_rsi(self, period: int) -> float | None:
        """RSI 계산."""
        if len(self._closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(-period, 0):
            change = self._closes[i] - self._closes[i - 1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _calc_sma(self, period: int) -> float | None:
        """SMA 계산."""
        if len(self._closes) < period:
            return None
        return sum(self._closes[-period:]) / period

    def _calc_sma_at(self, period: int, offset: int, length: int) -> float:
        """특정 offset에서의 SMA."""
        data = self._closes[:offset] if offset < 0 else self._closes
        if len(data) < length:
            return 0.0
        return sum(data[-length:]) / length

    def _calc_roc(self, period: int) -> float | None:
        """Rate of Change (%)."""
        if len(self._closes) < period + 1:
            return None
        prev = self._closes[-period - 1]
        if prev == 0:
            return None
        return (self._closes[-1] - prev) / prev * 100

    def _calc_atr(self, period: int) -> float | None:
        """ATR 계산."""
        if len(self._closes) < period + 1:
            return None
        trs = []
        for i in range(-period, 0):
            h = self._highs[i]
            l = self._lows[i]
            pc = self._closes[i - 1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        return sum(trs) / period

    def _calc_volume_ratio(self, period: int) -> float | None:
        """현재 거래량 / N-period 평균."""
        if len(self._volumes) < period + 1:
            return None
        avg = sum(self._volumes[-period - 1:-1]) / period
        if avg == 0:
            return None
        return self._volumes[-1] / avg


# ── 결과 출력 ──

def print_result(result: BacktestResult, strategy: str) -> None:
    """백테스트 결과를 출력한다."""
    print("\n" + "=" * 60)
    print(f"  BACKTEST RESULT: {strategy}")
    print("=" * 60)
    if result.start_date and result.end_date:
        print(f"  Period:     {result.start_date.strftime('%Y-%m-%d')} → {result.end_date.strftime('%Y-%m-%d')}")
    print(f"  Bars:       {result.total_bars:,}")
    print(f"  Trades:     {result.total_trades}")
    print(f"  Duration:   {result.duration_seconds:.1f}s")
    print("-" * 60)
    print(f"  Initial:    ${result.initial_balance:,.2f}")
    print(f"  Final:      ${result.final_equity:,.2f}")
    print(f"  PnL:        ${result.pnl:,.2f} ({result.pnl_pct:+.2f}%)")
    print(f"  Commission: ${result.total_commission:,.2f}")
    print(f"  Avg Slip:   {result.avg_slippage_pct:.4f}%")
    print("-" * 60)
    print(f"  Sharpe:     {result.sharpe_ratio:.2f}")
    print(f"  MDD:        -{result.max_drawdown_pct:.2f}%")
    print(f"  Win Rate:   {result.win_rate:.1f}%")
    print(f"  Profit F:   {result.profit_factor:.2f}")
    print("=" * 60)

    # Stage Gate 판정
    print("\n  STAGE GATE (ARCHITECTURE.md 13.3.3):")
    gates = [
        ("Sharpe > 1.0", result.sharpe_ratio > 1.0),
        ("MDD < 20%", result.max_drawdown_pct < 20),
        ("Win Rate > 50%", result.win_rate > 50),
        ("Profit Factor > 1.5", result.profit_factor > 1.5),
    ]
    all_pass = True
    for name, passed in gates:
        status = "PASS" if passed else "FAIL"
        mark = "✓" if passed else "✗"
        print(f"    {mark} {name}: {status}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("  >>> STAGE 1 PASSED — Ready for Stage 2 (Paper Trading)")
    else:
        print("  >>> STAGE 1 FAILED — Strategy needs adjustment")
    print()


# ── 메인 ──

async def run_backtest(
    data_path: Path,
    symbol: str,
    timeframe: str,
    strategy_type: str,
    initial_balance: float,
    commission: float,
    position_pct: float,
) -> BacktestResult:
    """백테스트를 실행한다."""
    # 1) CSV 로드
    bars = load_csv(data_path, symbol, timeframe)
    if not bars:
        print("ERROR: No data loaded.")
        sys.exit(1)
    print(f"Loaded {len(bars)} bars from {data_path}")

    # 2) 엔진 구성
    feed = HistoricalDataFeed(bars)
    broker = SimulatedBroker(
        initial_balance=initial_balance,
        commission_rate=commission,
    )
    dispatcher = EventDispatcher()

    # 3) 전략 등록
    strategy = RuleBasedStrategy(
        broker=broker,
        strategy_type=strategy_type,
        position_size_pct=position_pct,
    )
    dispatcher.on(EventType.BAR, strategy.on_bar)

    # 4) 실행
    engine = BacktestEngine(feed, broker, dispatcher)
    print(f"Running {strategy_type} strategy...")
    result = await engine.run()

    print_result(result, strategy_type)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="P.R.O.F.I.T. Stage 1 Backtest")
    parser.add_argument("--data", required=True, help="OHLCV CSV file path")
    parser.add_argument("--symbol", default="BTC/USDT", help="Symbol name")
    parser.add_argument("--timeframe", default="1h", help="Timeframe")
    parser.add_argument("--strategy", default="combined",
                        choices=["mean_reversion", "trend_following", "momentum", "breakout", "combined"],
                        help="Strategy type")
    parser.add_argument("--balance", type=float, default=10000, help="Initial balance (USDT)")
    parser.add_argument("--commission", type=float, default=0.001, help="Commission rate (0.001 = 0.1%%)")
    parser.add_argument("--position-pct", type=float, default=0.20, help="Position size %% of balance")
    parser.add_argument("--all-strategies", action="store_true", help="Run all strategies")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"ERROR: File not found: {data_path}")
        print(f"  Run first: python scripts/download_ohlcv.py --symbol {args.symbol} --timeframe {args.timeframe}")
        sys.exit(1)

    if args.all_strategies:
        strategies = ["mean_reversion", "trend_following", "momentum", "breakout", "combined"]
        for s in strategies:
            asyncio.run(run_backtest(
                data_path, args.symbol, args.timeframe, s,
                args.balance, args.commission, args.position_pct,
            ))
    else:
        asyncio.run(run_backtest(
            data_path, args.symbol, args.timeframe, args.strategy,
            args.balance, args.commission, args.position_pct,
        ))


if __name__ == "__main__":
    main()
