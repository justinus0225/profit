"""빌트인 전략 팩토리.

scripts/run_backtest.py의 RuleBasedStrategy를 분리하여
BacktestEngine과 StrategyRegistry 양쪽에서 재사용 가능하게 한다.

각 팩토리는 파라미터를 받아 EventType.BAR 핸들러를 반환한다.
파라미터는 StrategyConfig의 Field 범위와 일치하여 WFO에서 최적화 가능.
"""

from __future__ import annotations

from typing import Any

from src.core.event_engine import Event, OrderRequest, SimulatedBroker


class _StrategyState:
    """전략 실행에 필요한 공유 상태."""

    def __init__(self) -> None:
        self.closes: list[float] = []
        self.highs: list[float] = []
        self.lows: list[float] = []
        self.volumes: list[float] = []
        self.current_symbol: str = ""
        self.in_position: bool = False

    def update(self, event_data: dict[str, Any]) -> None:
        self.current_symbol = event_data["symbol"]
        self.closes.append(event_data["close"])
        self.highs.append(event_data["high"])
        self.lows.append(event_data["low"])
        self.volumes.append(event_data["volume"])


# ── 지표 계산 유틸리티 (순수 Python, 외부 의존성 없음) ──

def _calc_rsi(closes: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _calc_roc(closes: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    prev = closes[-period - 1]
    if prev == 0:
        return None
    return (closes[-1] - prev) / prev * 100


def _calc_atr(closes: list[float], highs: list[float], lows: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(-period, 0):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs) / period


def _calc_volume_ratio(volumes: list[float], period: int) -> float | None:
    if len(volumes) < period + 1:
        return None
    avg = sum(volumes[-period - 1:-1]) / period
    if avg == 0:
        return None
    return volumes[-1] / avg


async def _buy(broker: SimulatedBroker, state: _StrategyState, price: float, position_pct: float) -> None:
    balance = await broker.get_balance()
    invest = balance * position_pct
    qty = invest / price
    if qty <= 0:
        return
    try:
        await broker.submit_order(OrderRequest(
            symbol=state.current_symbol, side="buy",
            order_type="market", quantity=qty, price=price,
        ))
        state.in_position = True
    except ValueError:
        pass


async def _sell(broker: SimulatedBroker, state: _StrategyState, price: float) -> None:
    positions = await broker.get_positions()
    qty = positions.get(state.current_symbol, 0)
    if qty <= 0:
        state.in_position = False
        return
    await broker.submit_order(OrderRequest(
        symbol=state.current_symbol, side="sell",
        order_type="market", quantity=qty, price=price,
    ))
    state.in_position = False


# ── 전략 팩토리 ──

def create_mean_reversion_strategy(
    broker: SimulatedBroker,
    rsi_oversold: int = 30,
    rsi_overbought: int = 70,
    position_size_pct: float = 0.20,
) -> Any:
    """Mean Reversion 전략: RSI 과매도 매수, 과매수 매도."""
    state = _StrategyState()

    async def on_bar(event: Event) -> None:
        state.update(event.data)
        if len(state.closes) < 50:
            return
        rsi = _calc_rsi(state.closes, 14)
        if rsi is None:
            return
        if not state.in_position and rsi <= rsi_oversold:
            await _buy(broker, state, event.data["close"], position_size_pct)
        elif state.in_position and rsi >= rsi_overbought:
            await _sell(broker, state, event.data["close"])

    on_bar.__strategy_name__ = "mean_reversion"
    return on_bar


def create_trend_following_strategy(
    broker: SimulatedBroker,
    ma_short: int = 20,
    ma_long: int = 50,
    position_size_pct: float = 0.20,
) -> Any:
    """Trend Following 전략: MA 골든크로스/데드크로스."""
    state = _StrategyState()

    async def on_bar(event: Event) -> None:
        state.update(event.data)
        if len(state.closes) < ma_long + 1:
            return
        sma_s = _calc_sma(state.closes, ma_short)
        sma_l = _calc_sma(state.closes, ma_long)
        if sma_s is None or sma_l is None:
            return
        # 이전 바에서의 short MA
        prev_sma_s = _calc_sma(state.closes[:-1], ma_short)
        if not state.in_position and sma_s > sma_l and (prev_sma_s is None or prev_sma_s <= sma_l):
            await _buy(broker, state, event.data["close"], position_size_pct)
        elif state.in_position and sma_s < sma_l:
            await _sell(broker, state, event.data["close"])

    on_bar.__strategy_name__ = "trend_following"
    return on_bar


def create_momentum_strategy(
    broker: SimulatedBroker,
    roc_buy: float = 3.0,
    roc_sell: float = -2.0,
    position_size_pct: float = 0.20,
) -> Any:
    """Momentum 전략: ROC 양전환 매수."""
    state = _StrategyState()

    async def on_bar(event: Event) -> None:
        state.update(event.data)
        if len(state.closes) < 50:
            return
        roc = _calc_roc(state.closes, 12)
        if roc is None:
            return
        if not state.in_position and roc > roc_buy:
            await _buy(broker, state, event.data["close"], position_size_pct)
        elif state.in_position and roc < roc_sell:
            await _sell(broker, state, event.data["close"])

    on_bar.__strategy_name__ = "momentum"
    return on_bar


def create_breakout_strategy(
    broker: SimulatedBroker,
    lookback: int = 20,
    atr_multiplier: float = 2.0,
    position_size_pct: float = 0.20,
) -> Any:
    """Breakout 전략: N-bar 고점 돌파 + ATR 손절."""
    state = _StrategyState()

    async def on_bar(event: Event) -> None:
        state.update(event.data)
        if len(state.highs) < lookback + 1:
            return
        high_n = max(state.highs[-lookback - 1:-1])
        atr = _calc_atr(state.closes, state.highs, state.lows, 14)

        if not state.in_position and event.data["close"] > high_n:
            await _buy(broker, state, event.data["close"], position_size_pct)
        elif state.in_position and atr is not None:
            positions = await broker.get_positions()
            if state.current_symbol in positions:
                entry = broker._avg_prices.get(state.current_symbol, event.data["close"])
                if event.data["close"] < entry - atr * atr_multiplier:
                    await _sell(broker, state, event.data["close"])

    on_bar.__strategy_name__ = "breakout"
    return on_bar


def create_combined_strategy(
    broker: SimulatedBroker,
    rsi_buy: int = 40,
    rsi_sell: int = 75,
    ma_short: int = 20,
    ma_long: int = 50,
    volume_threshold: float = 1.5,
    min_signals: int = 2,
    position_size_pct: float = 0.20,
) -> Any:
    """Combined 전략: RSI + MA + Volume 복합."""
    state = _StrategyState()

    async def on_bar(event: Event) -> None:
        state.update(event.data)
        if len(state.closes) < ma_long + 1:
            return
        rsi = _calc_rsi(state.closes, 14)
        sma_s = _calc_sma(state.closes, ma_short)
        sma_l = _calc_sma(state.closes, ma_long)
        vol_ratio = _calc_volume_ratio(state.volumes, 20)

        if rsi is None or sma_s is None or sma_l is None:
            return

        buy_signals = 0
        if rsi < rsi_buy:
            buy_signals += 1
        if sma_s > sma_l:
            buy_signals += 1
        if vol_ratio is not None and vol_ratio > volume_threshold:
            buy_signals += 1

        if not state.in_position and buy_signals >= min_signals:
            await _buy(broker, state, event.data["close"], position_size_pct)
        elif state.in_position and (rsi > rsi_sell or sma_s < sma_l):
            await _sell(broker, state, event.data["close"])

    on_bar.__strategy_name__ = "combined"
    return on_bar


# 전략 팩토리 레지스트리 (이름 → 팩토리 함수)
STRATEGY_FACTORIES: dict[str, Any] = {
    "mean_reversion": create_mean_reversion_strategy,
    "trend_following": create_trend_following_strategy,
    "momentum": create_momentum_strategy,
    "breakout": create_breakout_strategy,
    "combined": create_combined_strategy,
}

# 전략별 WFO 파라미터 그리드 (기본값)
DEFAULT_PARAM_GRIDS: dict[str, dict[str, list[Any]]] = {
    "mean_reversion": {
        "rsi_oversold": [20, 25, 30, 35],
        "rsi_overbought": [65, 70, 75, 80],
    },
    "trend_following": {
        "ma_short": [10, 15, 20, 25],
        "ma_long": [40, 50, 60, 80],
    },
    "momentum": {
        "roc_buy": [2.0, 3.0, 4.0, 5.0],
        "roc_sell": [-3.0, -2.0, -1.0],
    },
    "breakout": {
        "lookback": [15, 20, 25, 30],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
    },
    "combined": {
        "rsi_buy": [30, 35, 40, 45],
        "rsi_sell": [70, 75, 80],
        "min_signals": [2, 3],
    },
}
