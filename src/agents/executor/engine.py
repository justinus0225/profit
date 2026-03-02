"""실행 에이전트 - 주문 실행, OMS 상태 관리, TWAP 분할.

ARCHITECTURE.md: Level 2, Execution Agent
- 포트폴리오 매니저 승인 → 실제 주문 생성/실행
- OMS 상태 머신 관리 (CREATED → SUBMITTED → FILLED)
- 대량 주문 TWAP 분할 (large_order_threshold 초과 시)
- 지정가 미체결 타임아웃 → 시장가 전환
- 슬리피지 모니터링
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.agents.executor.monitor import OrderMonitor
from src.agents.executor.oms import OrderStateMachine
from src.agents.executor.order import OrderBuilder
from src.exchange.client import ExchangeError
from src.exchange.models import OrderSide, OrderType

logger = logging.getLogger(__name__)


class ExecutorAgent(BaseAgent):
    """실행 에이전트: 주문 실행 + OMS 상태 관리."""

    @property
    def agent_type(self) -> str:
        return "executor"

    async def _on_initialize(self) -> None:
        self._exec_cfg = self._config.execution
        self._exec_schedule_cfg = self._config.schedule.execution
        self._oms_schedule_cfg = self._config.schedule.oms
        self._risk_cfg = self._config.risk

        # 미체결 주문 추적
        self._pending_orders: dict[str, dict[str, Any]] = {}

        # 모듈 초기화
        self._order_builder = OrderBuilder(self._exec_cfg)
        self._oms = OrderStateMachine()
        self._monitor = OrderMonitor(self._exec_cfg)

        # 이벤트 구독
        await self._subscribe("portfolio:trade_approved", self._on_trade_approved)
        await self._subscribe("portfolio:rebalance_required", self._on_rebalance_required)
        await self._subscribe("risk:stop_loss_triggered", self._on_stop_loss)
        await self._subscribe("risk:trailing_stop_triggered", self._on_trailing_stop)
        await self._subscribe("orchestrator:execute_order", self._on_execute_order)

    async def _on_run(self) -> None:
        reconcile_interval = self._oms_schedule_cfg.reconciliation_seconds
        poll_interval = self._exec_schedule_cfg.order_poll_seconds
        last_reconcile = time.time()

        while self._running:
            now = time.time()

            # 미체결 주문 타임아웃 체크
            expired = self._monitor.check_timeouts(self._pending_orders)
            for key in expired:
                order = self._pending_orders[key]
                self._oms.transition(order, "cancelled")
                order["cancelled_at"] = datetime.now(tz=timezone.utc).isoformat()
                await self._publish("executor:order_cancelled", order)

            # 거래소-OMS 상태 동기화
            if now - last_reconcile >= reconcile_interval:
                await self._reconcile_orders()
                last_reconcile = now

            await asyncio.sleep(poll_interval)

    # ── 주문 실행 ──

    async def _execute_buy(self, signal: dict[str, Any]) -> None:
        """매수 주문 실행."""
        symbol = signal.get("symbol", "")
        entry_price = signal.get("entry_price")
        position_size_usd = signal.get("position_size_usd", 0)
        if position_size_usd <= 0:
            logger.warning("[%s] Invalid position size for %s", self.name, symbol)
            return

        # 대량 주문 TWAP 분할 체크
        if position_size_usd > self._exec_cfg.large_order_threshold:
            await self._execute_twap(symbol, "buy", position_size_usd, entry_price)
        else:
            await self._submit_order(symbol, "buy", position_size_usd, entry_price, signal)

    async def _execute_sell(self, signal: dict[str, Any]) -> None:
        """매도 주문 실행."""
        symbol = signal.get("symbol", "")
        quantity = signal.get("quantity", 0)
        if quantity <= 0:
            logger.warning("[%s] Invalid quantity for sell %s", self.name, symbol)
            return
        await self._submit_order(symbol, "sell", 0, None, signal, quantity=quantity)

    async def _submit_order(
        self,
        symbol: str,
        side: str,
        total_usd: float,
        price: float | None,
        signal: dict[str, Any],
        quantity: float | None = None,
    ) -> None:
        """단일 주문 생성 및 거래소 제출."""
        order = self._order_builder.build(symbol, side, total_usd, price, signal, quantity)

        # OMS 상태: CREATED → SUBMITTED
        self._oms.transition(order, "submitted")
        order["submitted_at"] = datetime.now(tz=timezone.utc).isoformat()
        self._pending_orders[order["idempotency_key"]] = order

        await self._publish("executor:order_created", order)
        logger.info("[%s] Order submitted: %s %s %s (key=%s)",
                     self.name, side.upper(), symbol, order["order_type"],
                     order["idempotency_key"][:8])

        # 거래소 주문 전송
        if self._exchange_client is None:
            logger.warning("[%s] No exchange client — order not sent to exchange", self.name)
            return

        try:
            # 매수: total_usd / price → quantity 계산
            order_qty = quantity
            if order_qty is None and price and price > 0:
                order_qty = total_usd / price
            elif order_qty is None:
                # 시장가 매수: ticker 기준 수량 추정
                ticker = await self._exchange_client.fetch_ticker(symbol, agent_name=self.name)
                if ticker.last and ticker.last > 0:
                    order_qty = total_usd / ticker.last
                else:
                    logger.error("[%s] Cannot determine quantity for %s", self.name, symbol)
                    self._oms.transition(order, "cancelled")
                    return

            idem_key = uuid.UUID(order["idempotency_key"])
            order_type_enum = (
                OrderType.LIMIT if order["order_type"] == "limit" and price
                else OrderType.MARKET
            )

            exchange_order = await self._exchange_client.create_order(
                symbol=symbol,
                side=OrderSide(side),
                order_type=order_type_enum,
                quantity=order_qty,
                price=price if order_type_enum == OrderType.LIMIT else None,
                idempotency_key=idem_key,
                agent_name=self.name,
            )

            # OMS 업데이트
            order["exchange_order_id"] = exchange_order.exchange_order_id
            order["exchange_status"] = exchange_order.status
            order["filled"] = exchange_order.filled
            order["average_price"] = exchange_order.average

            if exchange_order.status == "closed":
                self._oms.transition(order, "filled")
                order["filled_at"] = datetime.now(tz=timezone.utc).isoformat()
                slippage = OrderMonitor.calculate_slippage(
                    price or 0, exchange_order.average or 0
                ) if price else 0
                order["slippage"] = slippage
                await self._publish("executor:order_filled", order)
                self._pending_orders.pop(order["idempotency_key"], None)
                logger.info("[%s] Order filled: %s %s avg=%.2f slip=%.4f",
                             self.name, side.upper(), symbol,
                             exchange_order.average or 0, slippage)
            elif exchange_order.filled and exchange_order.filled > 0:
                self._oms.transition(order, "partially_filled")

        except ExchangeError as e:
            logger.error("[%s] Exchange error: %s (retryable=%s)",
                          self.name, e, e.retryable)
            if not e.retryable:
                self._oms.transition(order, "cancelled")
                order["error"] = str(e)
                await self._publish("executor:order_failed", order)
                self._pending_orders.pop(order["idempotency_key"], None)

    async def _execute_twap(
        self, symbol: str, side: str, total_usd: float, price: float | None
    ) -> None:
        """TWAP (Time-Weighted Average Price) 분할 주문."""
        intervals = self._exec_cfg.twap.intervals
        interval_seconds = self._exec_cfg.twap.interval_seconds
        slice_usd = total_usd / intervals

        logger.info("[%s] TWAP: %s %s $%.0f in %d slices",
                     self.name, side.upper(), symbol, total_usd, intervals)

        for i in range(intervals):
            signal = {
                "signal_id": f"TWAP-{i+1}/{intervals}",
                "symbol": symbol,
                "position_size_usd": slice_usd,
            }
            await self._submit_order(symbol, side, slice_usd, price, signal)

            if i < intervals - 1:
                await asyncio.sleep(interval_seconds)

    # ── 거래소 조정 ──

    async def _reconcile_orders(self) -> None:
        """미체결 주문을 거래소와 동기화한다."""
        if self._exchange_client is None:
            self._monitor.reconcile(self._pending_orders)
            return

        from src.data.models.order import OrderState

        to_remove: list[str] = []
        for key, order in self._pending_orders.items():
            if order.get("state") not in (
                OrderState.SUBMITTED.value, OrderState.PARTIALLY_FILLED.value
            ):
                continue
            ex_id = order.get("exchange_order_id")
            if not ex_id:
                continue
            try:
                ex_order = await self._exchange_client.fetch_order(
                    ex_id, order["symbol"], agent_name=self.name,
                )
                order["filled"] = ex_order.filled
                order["average_price"] = ex_order.average
                order["exchange_status"] = ex_order.status

                if ex_order.status == "closed":
                    self._oms.transition(order, "filled")
                    order["filled_at"] = datetime.now(tz=timezone.utc).isoformat()
                    await self._publish("executor:order_filled", order)
                    to_remove.append(key)
                elif ex_order.status == "canceled":
                    self._oms.transition(order, "cancelled")
                    await self._publish("executor:order_cancelled", order)
                    to_remove.append(key)
            except ExchangeError:
                logger.debug("[%s] Reconcile fetch failed: %s", self.name, ex_id)

        for key in to_remove:
            self._pending_orders.pop(key, None)

        if self._pending_orders:
            logger.info("[%s] Reconciliation: %d pending orders",
                         self.name, len(self._pending_orders))

    # ── 이벤트 핸들러 ──

    async def _on_trade_approved(self, data: dict[str, Any]) -> None:
        """포트폴리오 매니저 승인 → 매수 실행."""
        logger.info("[%s] Trade approved: %s", self.name, data.get("symbol"))
        await self._execute_buy(data)

    async def _on_rebalance_required(self, data: dict[str, Any]) -> None:
        """리밸런싱 매도 요청."""
        if data.get("action") == "close":
            await self._execute_sell(data)

    async def _on_stop_loss(self, data: dict[str, Any]) -> None:
        """손절 트리거 → 긴급 매도."""
        logger.warning("[%s] Stop-loss sell: %s", self.name, data.get("symbol"))
        data["quantity"] = data.get("position_quantity", 0)
        await self._execute_sell(data)

    async def _on_trailing_stop(self, data: dict[str, Any]) -> None:
        """트레일링 스탑 트리거 → 매도."""
        logger.info("[%s] Trailing stop sell: %s", self.name, data.get("symbol"))
        data["quantity"] = data.get("position_quantity", 0)
        await self._execute_sell(data)

    async def _on_execute_order(self, data: dict[str, Any]) -> None:
        """오케스트레이터 직접 주문 요청."""
        side = data.get("side", "buy")
        if side == "buy":
            await self._execute_buy(data)
        else:
            await self._execute_sell(data)

    async def _on_stop(self) -> None:
        """정지 시 미체결 주문 로그."""
        from src.data.models.order import OrderState

        pending = [
            o for o in self._pending_orders.values()
            if o.get("state") in (
                OrderState.SUBMITTED.value, OrderState.PARTIALLY_FILLED.value
            )
        ]
        if pending:
            logger.warning("[%s] Stopping with %d pending orders",
                            self.name, len(pending))
