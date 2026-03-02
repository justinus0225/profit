"""통합 이벤트 엔진 단위 테스트 (P2)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.event_engine import (
    BacktestEngine,
    BacktestResult,
    Bar,
    Event,
    EventDispatcher,
    EventType,
    HistoricalDataFeed,
    OrderRequest,
    SimulatedBroker,
    VolumeBasedSlippage,
)


# ── 테스트 데이터 ──


def _make_bars(n: int = 10, base_price: float = 100.0) -> list[Bar]:
    """테스트용 Bar 데이터 생성 (가격 상승 추세)."""
    bars = []
    for i in range(n):
        price = base_price + i * 1.0
        bars.append(
            Bar(
                symbol="BTC/KRW",
                timestamp=datetime(2025, 1, 1, i, 0, tzinfo=timezone.utc),
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price + 0.3,
                volume=1000.0 + i * 100,
            )
        )
    return bars


# ── HistoricalDataFeed 테스트 ──


class TestHistoricalDataFeed:
    @pytest.mark.asyncio
    async def test_bar_iteration(self) -> None:
        bars = _make_bars(5)
        feed = HistoricalDataFeed(bars)
        await feed.start()

        collected = []
        while True:
            bar = await feed.next_bar()
            if bar is None:
                break
            collected.append(bar)

        assert len(collected) == 5
        assert collected[0].timestamp < collected[-1].timestamp

    @pytest.mark.asyncio
    async def test_is_not_live(self) -> None:
        feed = HistoricalDataFeed([])
        assert feed.is_live is False

    @pytest.mark.asyncio
    async def test_progress(self) -> None:
        bars = _make_bars(4)
        feed = HistoricalDataFeed(bars)
        await feed.start()

        assert feed.progress == 0.0
        await feed.next_bar()
        assert feed.progress == pytest.approx(0.25)
        await feed.next_bar()
        assert feed.progress == pytest.approx(0.50)

    @pytest.mark.asyncio
    async def test_total_bars(self) -> None:
        bars = _make_bars(7)
        feed = HistoricalDataFeed(bars)
        assert feed.total_bars == 7

    @pytest.mark.asyncio
    async def test_empty_feed(self) -> None:
        feed = HistoricalDataFeed([])
        await feed.start()
        assert await feed.next_bar() is None


# ── SimulatedBroker 테스트 ──


class TestSimulatedBroker:
    @pytest.mark.asyncio
    async def test_initial_balance(self) -> None:
        broker = SimulatedBroker(initial_balance=100_000.0)
        assert await broker.get_balance() == 100_000.0

    @pytest.mark.asyncio
    async def test_buy_order(self) -> None:
        broker = SimulatedBroker(initial_balance=100_000.0, commission_rate=0.001)
        order = OrderRequest(
            symbol="BTC/KRW",
            side="buy",
            order_type="market",
            quantity=1.0,
            price=50_000.0,
        )
        fill = await broker.submit_order(order)

        assert fill.symbol == "BTC/KRW"
        assert fill.side == "buy"
        assert fill.quantity == 1.0
        assert fill.commission > 0

        balance = await broker.get_balance()
        assert balance < 100_000.0

    @pytest.mark.asyncio
    async def test_sell_order(self) -> None:
        broker = SimulatedBroker(initial_balance=100_000.0, commission_rate=0.001)

        # 먼저 매수
        buy_order = OrderRequest(
            symbol="BTC/KRW", side="buy", order_type="market",
            quantity=1.0, price=50_000.0,
        )
        await broker.submit_order(buy_order)

        # 매도
        sell_order = OrderRequest(
            symbol="BTC/KRW", side="sell", order_type="market",
            quantity=1.0, price=50_000.0,
        )
        fill = await broker.submit_order(sell_order)
        assert fill.side == "sell"

        positions = await broker.get_positions()
        assert positions.get("BTC/KRW", 0) == 0

    @pytest.mark.asyncio
    async def test_commission_deduction(self) -> None:
        broker = SimulatedBroker(
            initial_balance=100_000.0, commission_rate=0.001
        )
        order = OrderRequest(
            symbol="BTC/KRW", side="buy", order_type="market",
            quantity=1.0, price=10_000.0,
        )
        fill = await broker.submit_order(order)
        # 커미션 = ~10,000 * 0.001 = ~10
        assert fill.commission == pytest.approx(10.0, abs=2.0)

    @pytest.mark.asyncio
    async def test_fills_tracking(self) -> None:
        broker = SimulatedBroker(initial_balance=100_000.0)
        order = OrderRequest(
            symbol="BTC/KRW", side="buy", order_type="market",
            quantity=0.5, price=50_000.0,
        )
        await broker.submit_order(order)
        assert len(broker.fills) == 1

    @pytest.mark.asyncio
    async def test_pnl_properties(self) -> None:
        broker = SimulatedBroker(initial_balance=100_000.0, commission_rate=0.0)
        order = OrderRequest(
            symbol="BTC/KRW", side="buy", order_type="market",
            quantity=1.0, price=50_000.0,
        )
        await broker.submit_order(order)
        # 잔고 감소하지만 equity는 포지션 포함
        assert broker.total_equity == pytest.approx(100_000.0, abs=100.0)


# ── VolumeBasedSlippage 테스트 ──


class TestVolumeBasedSlippage:
    def test_slippage_calculation(self) -> None:
        model = VolumeBasedSlippage(base_bps=5, impact_factor=0.1)
        # calculate(side, price, quantity)
        slippage = model.calculate("buy", 100.0, 1.0)
        assert slippage > 0

    def test_zero_quantity(self) -> None:
        model = VolumeBasedSlippage(base_bps=5, impact_factor=0.1)
        slippage = model.calculate("buy", 100.0, 0.0)
        assert slippage >= 0


# ── EventDispatcher 테스트 ──


class TestEventDispatcher:
    @pytest.mark.asyncio
    async def test_register_and_emit(self) -> None:
        dispatcher = EventDispatcher()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        dispatcher.on(EventType.BAR, handler)

        event = Event(
            type=EventType.BAR,
            timestamp=datetime.now(tz=timezone.utc),
            data={"price": 100},
        )
        await dispatcher.emit(event)

        assert len(received) == 1
        assert received[0].data["price"] == 100

    @pytest.mark.asyncio
    async def test_multiple_handlers(self) -> None:
        dispatcher = EventDispatcher()
        results: list[str] = []

        async def handler_a(event: Event) -> None:
            results.append("a")

        async def handler_b(event: Event) -> None:
            results.append("b")

        dispatcher.on(EventType.FILL, handler_a)
        dispatcher.on(EventType.FILL, handler_b)

        event = Event(
            type=EventType.FILL,
            timestamp=datetime.now(tz=timezone.utc),
        )
        await dispatcher.emit(event)
        assert set(results) == {"a", "b"}


# ── BacktestEngine 통합 테스트 ──


class TestBacktestEngine:
    @pytest.mark.asyncio
    async def test_basic_backtest(self) -> None:
        bars = _make_bars(20, base_price=100.0)
        feed = HistoricalDataFeed(bars)
        broker = SimulatedBroker(initial_balance=100_000.0)
        dispatcher = EventDispatcher()

        # 이벤트 핸들러에서 close 값을 기반으로 매매
        trade_state = {"bought": False}

        async def strategy(event: Event) -> None:
            close = event.data.get("close", 0)
            if not trade_state["bought"] and close > 104:
                order = OrderRequest(
                    symbol="BTC/KRW",
                    side="buy",
                    order_type="market",
                    quantity=1.0,
                    price=close,
                )
                await broker.submit_order(order)
                trade_state["bought"] = True
            elif trade_state["bought"] and close > 115:
                order = OrderRequest(
                    symbol="BTC/KRW",
                    side="sell",
                    order_type="market",
                    quantity=1.0,
                    price=close,
                )
                await broker.submit_order(order)
                trade_state["bought"] = False

        dispatcher.on(EventType.BAR, strategy)

        engine = BacktestEngine(feed, broker, dispatcher)
        result = await engine.run()

        assert isinstance(result, BacktestResult)
        assert result.total_bars == 20
        assert result.initial_balance == 100_000.0
        assert len(result.equity_curve) > 0

    @pytest.mark.asyncio
    async def test_empty_backtest(self) -> None:
        feed = HistoricalDataFeed([])
        broker = SimulatedBroker(initial_balance=50_000.0)
        dispatcher = EventDispatcher()

        engine = BacktestEngine(feed, broker, dispatcher)
        result = await engine.run()

        assert result.total_bars == 0
        assert result.total_trades == 0
