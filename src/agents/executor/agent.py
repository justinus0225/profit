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
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.data.models.order import OrderState

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
            await self._check_pending_timeouts()

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
            await self._execute_single_order(symbol, "buy", position_size_usd, entry_price, signal)

    async def _execute_sell(self, signal: dict[str, Any]) -> None:
        """매도 주문 실행."""
        symbol = signal.get("symbol", "")
        quantity = signal.get("quantity", 0)
        if quantity <= 0:
            logger.warning("[%s] Invalid quantity for sell %s", self.name, symbol)
            return
        await self._execute_single_order(symbol, "sell", 0, None, signal, quantity=quantity)

    async def _execute_single_order(
        self,
        symbol: str,
        side: str,
        total_usd: float,
        price: float | None,
        signal: dict[str, Any],
        quantity: float | None = None,
    ) -> None:
        """단일 주문 생성 및 실행."""
        order_type = self._exec_cfg.default_order_type
        idempotency_key = str(uuid.uuid4())

        order = {
            "order_id": f"ORD-{datetime.now(tz=timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}",
            "idempotency_key": idempotency_key,
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "total_usd": total_usd,
            "price": price,
            "signal_id": signal.get("signal_id"),
            "state": OrderState.CREATED.value,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        # OMS 상태: CREATED → SUBMITTED (검증된 전이)
        self._update_order_state(idempotency_key, OrderState.SUBMITTED, order)
        order["submitted_at"] = datetime.now(tz=timezone.utc).isoformat()
        self._pending_orders[idempotency_key] = order

        await self._publish("executor:order_created", order)
        logger.info("[%s] Order created: %s %s %s (key=%s)",
                     self.name, side.upper(), symbol, order_type, idempotency_key[:8])

        # 실제 거래소 주문은 ExchangeClient 연동 시 실행
        # 현재는 OMS 상태 관리 프레임워크만 제공

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
            await self._execute_single_order(symbol, side, slice_usd, price, signal)

            if i < intervals - 1:
                await asyncio.sleep(interval_seconds)

    # ── 주문 상태 관리 ──

    async def _check_pending_timeouts(self) -> None:
        """미체결 주문 타임아웃 체크 → 시장가 전환 또는 취소."""
        timeout = self._exec_cfg.limit_order_timeout
        now = time.time()
        expired: list[str] = []

        for key, order in self._pending_orders.items():
            if order.get("state") != OrderState.SUBMITTED.value:
                continue
            submitted = order.get("submitted_at", "")
            if not submitted:
                continue
            submitted_dt = datetime.fromisoformat(submitted)
            elapsed = now - submitted_dt.timestamp()
            if elapsed >= timeout:
                expired.append(key)

        for key in expired:
            order = self._pending_orders[key]
            logger.warning("[%s] Order timeout: %s %s (elapsed=%ds)",
                            self.name, order.get("symbol"), key[:8], timeout)
            self._update_order_state(key, OrderState.CANCELLED, order)
            order["cancelled_at"] = datetime.now(tz=timezone.utc).isoformat()
            await self._publish("executor:order_cancelled", order)

    async def _reconcile_orders(self) -> None:
        """거래소-OMS 상태 동기화 (미체결 주문 확인)."""
        pending = [o for o in self._pending_orders.values()
                   if o.get("state") in (OrderState.SUBMITTED.value, OrderState.PARTIALLY_FILLED.value)]
        if pending:
            logger.info("[%s] Reconciliation: %d pending orders", self.name, len(pending))
        # 실제 거래소 조회는 ExchangeClient 연동 시 구현

    def _update_order_state(
        self, key: str, new_state: OrderState, order: dict[str, Any] | None = None
    ) -> bool:
        """OMS 상태 전이 (유효성 검증 포함). 성공 시 True 반환."""
        if order is None:
            order = self._pending_orders.get(key)
        if not order:
            return False
        current = OrderState(order["state"])
        from src.data.models.order import VALID_TRANSITIONS
        if new_state not in VALID_TRANSITIONS.get(current, set()):
            logger.error("[%s] Invalid transition: %s → %s", self.name, current.value, new_state.value)
            return False
        order["state"] = new_state.value
        return True

    async def _publish_order_filled(self, order: dict[str, Any], fill_price: float) -> None:
        """주문 체결 이벤트 발행 (리스크 매니저 P&L 추적용)."""
        slippage = self._calculate_slippage(order.get("price", 0) or 0, fill_price)
        payload = {
            "order_id": order.get("order_id"),
            "exchange_order_id": order.get("exchange_order_id"),
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "filled_quantity": order.get("quantity"),
            "filled_price": fill_price,
            "total_usd": order.get("total_usd", 0),
            "fee_usd": 0,
            "slippage_pct": slippage,
            "realized_pnl": 0,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        await self._publish("executor:order_filled", payload)

    # ── 슬리피지 계산 ──

    def _calculate_slippage(self, expected_price: float, actual_price: float) -> float:
        """슬리피지 계산."""
        if expected_price <= 0:
            return 0.0
        return (actual_price - expected_price) / expected_price

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
        pending = [o for o in self._pending_orders.values()
                   if o.get("state") in (OrderState.SUBMITTED.value, OrderState.PARTIALLY_FILLED.value)]
        if pending:
            logger.warning("[%s] Stopping with %d pending orders", self.name, len(pending))
