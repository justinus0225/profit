"""통합 이벤트 엔진 (ARCHITECTURE.md P2).

백테스트와 라이브 실행이 동일한 코드 경로를 사용하도록
추상 DataFeed / Broker / EventDispatcher를 제공한다.

핵심 원칙:
- 전략 코드는 백테스트/라이브 구분 없이 동일하게 실행
- Look-Ahead Bias 방지: DataFeed가 시간 순서대로만 데이터 제공
- 현실적 시뮬레이션: 슬리피지, 커미션, 마켓 임팩트 모델 포함
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


# ── 이벤트 타입 ──

class EventType(str, Enum):
    TICK = "tick"
    BAR = "bar"
    ORDER = "order"
    FILL = "fill"
    POSITION = "position"
    SIGNAL = "signal"


@dataclass
class Event:
    """시스템 이벤트."""

    type: EventType
    timestamp: datetime
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Bar:
    """OHLCV 캔들 바."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str = "1h"


@dataclass
class Tick:
    """실시간 틱 데이터."""

    symbol: str
    timestamp: datetime
    price: float
    volume: float = 0.0
    bid: float = 0.0
    ask: float = 0.0


@dataclass
class OrderRequest:
    """주문 요청."""

    symbol: str
    side: str  # "buy" | "sell"
    order_type: str  # "market" | "limit"
    quantity: float
    price: float | None = None
    client_order_id: str = ""


@dataclass
class Fill:
    """체결 결과."""

    order_id: str
    symbol: str
    side: str
    quantity: float
    fill_price: float
    commission: float
    slippage: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


# ── 추상 DataFeed ──

class DataFeed(ABC):
    """데이터 소스 추상 인터페이스.

    Historical(백테스트)과 Live(실시간) 모두 동일 인터페이스로 전략에 데이터 전달.
    """

    @abstractmethod
    async def start(self) -> None:
        """데이터 수집 시작."""

    @abstractmethod
    async def stop(self) -> None:
        """데이터 수집 중지."""

    @abstractmethod
    async def next_bar(self) -> Bar | None:
        """다음 캔들 바를 반환 (없으면 None)."""

    @abstractmethod
    async def next_tick(self) -> Tick | None:
        """다음 틱을 반환 (없으면 None)."""

    @property
    @abstractmethod
    def is_live(self) -> bool:
        """라이브 피드 여부."""


class HistoricalDataFeed(DataFeed):
    """과거 데이터 피드 (백테스트용).

    시간순 정렬된 OHLCV 데이터를 하나씩 공급한다.
    Look-Ahead Bias 방지: 현재 인덱스 이후 데이터에 접근 불가.
    """

    def __init__(self, bars: list[Bar]) -> None:
        self._bars = sorted(bars, key=lambda b: b.timestamp)
        self._index = 0

    async def start(self) -> None:
        self._index = 0

    async def stop(self) -> None:
        pass

    async def next_bar(self) -> Bar | None:
        if self._index >= len(self._bars):
            return None
        bar = self._bars[self._index]
        self._index += 1
        return bar

    async def next_tick(self) -> Tick | None:
        bar = await self.next_bar()
        if bar is None:
            return None
        return Tick(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            price=bar.close,
            volume=bar.volume,
        )

    @property
    def is_live(self) -> bool:
        return False

    @property
    def total_bars(self) -> int:
        return len(self._bars)

    @property
    def progress(self) -> float:
        if not self._bars:
            return 1.0
        return self._index / len(self._bars)


# ── 추상 Broker ──

class Broker(ABC):
    """주문 실행 추상 인터페이스.

    SimulatedBroker(백테스트/Paper) / LiveBroker(실제 거래소)로 분기.
    """

    @abstractmethod
    async def submit_order(self, order: OrderRequest) -> Fill:
        """주문을 전송하고 체결 결과를 반환한다."""

    @abstractmethod
    async def get_balance(self) -> float:
        """가용 잔고(USDT)를 반환한다."""

    @abstractmethod
    async def get_positions(self) -> dict[str, float]:
        """보유 포지션 {symbol: quantity}를 반환한다."""


class SimulatedBroker(Broker):
    """시뮬레이션 브로커 (백테스트/Paper Trading).

    현실적 시뮬레이션을 위한 모델:
    - Volume-based 슬리피지 모델
    - 커미션 모델 (0.1% 기본)
    - 마켓 임팩트 모델
    """

    def __init__(
        self,
        initial_balance: float = 1_000_000.0,
        commission_rate: float = 0.001,  # 0.1%
        slippage_model: SlippageModel | None = None,
    ) -> None:
        self._balance = initial_balance
        self._initial_balance = initial_balance
        self._commission_rate = commission_rate
        self._slippage = slippage_model or VolumeBasedSlippage()
        self._positions: dict[str, float] = {}  # symbol → quantity
        self._avg_prices: dict[str, float] = {}  # symbol → avg entry price
        self._order_count = 0
        self._fills: list[Fill] = []

    async def submit_order(self, order: OrderRequest) -> Fill:
        """시뮬레이션 체결 처리."""
        self._order_count += 1
        base_price = order.price or 0.0
        slippage = self._slippage.calculate(order.side, base_price, order.quantity)

        if order.side == "buy":
            fill_price = base_price + slippage
        else:
            fill_price = base_price - slippage

        fill_price = max(fill_price, 0.001)  # 최소 가격 보장
        total = fill_price * order.quantity
        commission = total * self._commission_rate

        if order.side == "buy":
            cost = total + commission
            if cost > self._balance:
                # 잔고 부족: 가능한 만큼만 매수
                affordable_qty = (self._balance / (fill_price * (1 + self._commission_rate)))
                if affordable_qty <= 0:
                    raise ValueError(f"Insufficient balance: {self._balance:.2f}")
                order = OrderRequest(
                    symbol=order.symbol, side="buy", order_type=order.order_type,
                    quantity=affordable_qty, price=order.price,
                    client_order_id=order.client_order_id,
                )
                total = fill_price * affordable_qty
                commission = total * self._commission_rate
                cost = total + commission

            self._balance -= cost
            prev_qty = self._positions.get(order.symbol, 0)
            prev_avg = self._avg_prices.get(order.symbol, 0)
            new_qty = prev_qty + order.quantity
            if new_qty > 0:
                self._avg_prices[order.symbol] = (
                    (prev_avg * prev_qty + fill_price * order.quantity) / new_qty
                )
            self._positions[order.symbol] = new_qty
        else:
            # 매도
            self._balance += total - commission
            prev_qty = self._positions.get(order.symbol, 0)
            self._positions[order.symbol] = max(0, prev_qty - order.quantity)
            if self._positions[order.symbol] == 0:
                self._positions.pop(order.symbol, None)
                self._avg_prices.pop(order.symbol, None)

        fill = Fill(
            order_id=f"SIM-{self._order_count}",
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            fill_price=fill_price,
            commission=commission,
            slippage=slippage,
        )
        self._fills.append(fill)
        return fill

    async def get_balance(self) -> float:
        return self._balance

    async def get_positions(self) -> dict[str, float]:
        return dict(self._positions)

    @property
    def total_equity(self) -> float:
        """총 자산 가치 (현금 + 포지션 평가)."""
        equity = self._balance
        for symbol, qty in self._positions.items():
            avg = self._avg_prices.get(symbol, 0)
            equity += qty * avg
        return equity

    @property
    def pnl(self) -> float:
        return self.total_equity - self._initial_balance

    @property
    def pnl_pct(self) -> float:
        if self._initial_balance == 0:
            return 0.0
        return self.pnl / self._initial_balance * 100

    @property
    def fills(self) -> list[Fill]:
        return list(self._fills)


# ── 슬리피지 모델 ──

class SlippageModel(ABC):
    """슬리피지 모델 추상 인터페이스."""

    @abstractmethod
    def calculate(self, side: str, price: float, quantity: float) -> float:
        """슬리피지 금액을 반환한다."""


class VolumeBasedSlippage(SlippageModel):
    """Volume-based 슬리피지 모델 (ARCHITECTURE.md P2).

    주문 크기에 비례한 슬리피지를 시뮬레이션한다.
    """

    def __init__(self, base_bps: float = 5.0, impact_factor: float = 0.1) -> None:
        self._base_bps = base_bps  # 기본 슬리피지 (bps)
        self._impact_factor = impact_factor

    def calculate(self, side: str, price: float, quantity: float) -> float:
        order_value = price * quantity
        # 기본 슬리피지 + 주문 크기 비례 임팩트
        bps = self._base_bps + self._impact_factor * (order_value / 10000)
        slippage = price * bps / 10000
        return abs(slippage)


# ── 이벤트 디스패처 ──

EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventDispatcher:
    """이벤트 디스패처.

    on_tick(), on_bar(), on_order(), on_fill() 이벤트를
    등록된 핸들러에 비동기적으로 전달한다.
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = {}

    def on(self, event_type: EventType, handler: EventHandler) -> None:
        """이벤트 핸들러 등록."""
        self._handlers.setdefault(event_type, []).append(handler)

    async def emit(self, event: Event) -> None:
        """이벤트 발행 → 등록된 핸들러 순차 호출."""
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            await handler(event)


# ── 백테스트 엔진 ──

@dataclass
class BacktestResult:
    """백테스트 실행 결과."""

    start_date: datetime | None = None
    end_date: datetime | None = None
    total_bars: int = 0
    total_trades: int = 0
    initial_balance: float = 0.0
    final_equity: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_commission: float = 0.0
    avg_slippage_pct: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    duration_seconds: float = 0.0


class BacktestEngine:
    """백테스트 실행 엔진 (ARCHITECTURE.md P2).

    HistoricalDataFeed + SimulatedBroker + 전략 조합으로
    과거 데이터에서 전략 성과를 시뮬레이션한다.

    사용법:
        engine = BacktestEngine(data_feed, broker, dispatcher)
        result = await engine.run()
    """

    def __init__(
        self,
        data_feed: DataFeed,
        broker: SimulatedBroker,
        dispatcher: EventDispatcher,
    ) -> None:
        self._feed = data_feed
        self._broker = broker
        self._dispatcher = dispatcher
        self._equity_curve: list[float] = []
        self._peak_equity: float = 0.0
        self._max_drawdown: float = 0.0

    async def run(self) -> BacktestResult:
        """백테스트를 실행하고 결과를 반환한다."""
        start_time = time.monotonic()
        await self._feed.start()

        start_date: datetime | None = None
        end_date: datetime | None = None
        bar_count = 0

        while True:
            bar = await self._feed.next_bar()
            if bar is None:
                break

            if start_date is None:
                start_date = bar.timestamp
            end_date = bar.timestamp
            bar_count += 1

            # BAR 이벤트 발행
            event = Event(
                type=EventType.BAR,
                timestamp=bar.timestamp,
                data={
                    "symbol": bar.symbol,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "timeframe": bar.timeframe,
                },
            )
            await self._dispatcher.emit(event)

            # 에쿼티 곡선 기록
            equity = self._broker.total_equity
            self._equity_curve.append(equity)
            if equity > self._peak_equity:
                self._peak_equity = equity
            if self._peak_equity > 0:
                dd = (self._peak_equity - equity) / self._peak_equity
                self._max_drawdown = max(self._max_drawdown, dd)

        await self._feed.stop()
        duration = time.monotonic() - start_time

        fills = self._broker.fills
        return BacktestResult(
            start_date=start_date,
            end_date=end_date,
            total_bars=bar_count,
            total_trades=len(fills),
            initial_balance=self._broker._initial_balance,
            final_equity=self._broker.total_equity,
            pnl=self._broker.pnl,
            pnl_pct=self._broker.pnl_pct,
            max_drawdown_pct=round(self._max_drawdown * 100, 2),
            sharpe_ratio=self._calculate_sharpe(),
            win_rate=self._calculate_win_rate(fills),
            profit_factor=self._calculate_profit_factor(fills),
            total_commission=sum(f.commission for f in fills),
            avg_slippage_pct=self._calculate_avg_slippage(fills),
            equity_curve=self._equity_curve,
            fills=fills,
            duration_seconds=round(duration, 2),
        )

    def _calculate_sharpe(self, risk_free_rate: float = 0.0) -> float:
        """Sharpe Ratio 계산 (연율화)."""
        if len(self._equity_curve) < 2:
            return 0.0
        returns = []
        for i in range(1, len(self._equity_curve)):
            r = (self._equity_curve[i] - self._equity_curve[i - 1]) / self._equity_curve[i - 1]
            returns.append(r)
        if not returns:
            return 0.0
        import math
        avg = sum(returns) / len(returns)
        variance = sum((r - avg) ** 2 for r in returns) / len(returns)
        std = math.sqrt(variance) if variance > 0 else 0.001
        # 연율화 (hourly → yearly: sqrt(8760))
        return round((avg - risk_free_rate) / std * math.sqrt(8760), 2)

    def _calculate_win_rate(self, fills: list[Fill]) -> float:
        """승률 계산."""
        if not fills:
            return 0.0
        # 매수→매도 쌍을 추적
        trades: list[float] = []
        buy_fills: dict[str, list[Fill]] = {}
        for f in fills:
            if f.side == "buy":
                buy_fills.setdefault(f.symbol, []).append(f)
            elif f.side == "sell" and f.symbol in buy_fills and buy_fills[f.symbol]:
                entry = buy_fills[f.symbol].pop(0)
                pnl = (f.fill_price - entry.fill_price) * f.quantity
                pnl -= entry.commission + f.commission
                trades.append(pnl)
        if not trades:
            return 0.0
        wins = sum(1 for t in trades if t > 0)
        return round(wins / len(trades) * 100, 1)

    def _calculate_profit_factor(self, fills: list[Fill]) -> float:
        """손익비 계산."""
        buy_fills: dict[str, list[Fill]] = {}
        gross_profit = 0.0
        gross_loss = 0.0
        for f in fills:
            if f.side == "buy":
                buy_fills.setdefault(f.symbol, []).append(f)
            elif f.side == "sell" and f.symbol in buy_fills and buy_fills[f.symbol]:
                entry = buy_fills[f.symbol].pop(0)
                pnl = (f.fill_price - entry.fill_price) * f.quantity
                pnl -= entry.commission + f.commission
                if pnl > 0:
                    gross_profit += pnl
                else:
                    gross_loss += abs(pnl)
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return round(gross_profit / gross_loss, 2)

    def _calculate_avg_slippage(self, fills: list[Fill]) -> float:
        """평균 슬리피지 (%)."""
        if not fills:
            return 0.0
        slippages = []
        for f in fills:
            if f.fill_price > 0:
                slippages.append(f.slippage / f.fill_price * 100)
        if not slippages:
            return 0.0
        return round(sum(slippages) / len(slippages), 4)
