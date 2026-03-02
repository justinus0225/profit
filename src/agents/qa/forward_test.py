"""Paper Trading 검증 모듈.

실제 거래 전 Paper Trading 모드에서 신호 품질을 검증한다.
가상 포트폴리오를 운영하며 신호 정확도, 수익률, 리스크를 평가한다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class ForwardTester:
    """Paper Trading 기반 신호 품질 검증."""

    def __init__(self, initial_balance: float = 100_000.0) -> None:
        self._initial_balance = initial_balance
        self._balance = initial_balance
        self._positions: dict[str, dict[str, Any]] = {}
        self._closed_trades: list[dict[str, Any]] = []
        self._signals_received: int = 0
        self._signals_executed: int = 0

    def receive_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        """매매 신호를 수신하고 가상 실행한다.

        Returns:
            실행 결과 dict.
        """
        self._signals_received += 1
        symbol = signal.get("symbol", "")
        direction = signal.get("direction", "")
        entry_price = signal.get("entry_price", 0)

        if direction == "BUY" and symbol not in self._positions:
            position_size = min(
                self._balance * 0.1, signal.get("position_size_usd", 10_000)
            )
            if position_size <= 0 or entry_price <= 0:
                return {"executed": False, "reason": "Invalid size or price"}

            quantity = position_size / entry_price
            self._positions[symbol] = {
                "symbol": symbol,
                "entry_price": entry_price,
                "quantity": quantity,
                "size_usd": position_size,
                "signal_id": signal.get("signal_id"),
                "target_price": signal.get("target_price"),
                "stop_loss_price": signal.get("stop_loss_price"),
                "opened_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            self._balance -= position_size
            self._signals_executed += 1
            return {"executed": True, "action": "BUY", "symbol": symbol}

        if direction == "SELL" and symbol in self._positions:
            pos = self._positions.pop(symbol)
            pnl = (entry_price - pos["entry_price"]) * pos["quantity"]
            pnl_pct = (entry_price - pos["entry_price"]) / pos["entry_price"]
            self._balance += pos["size_usd"] + pnl

            trade = {
                "symbol": symbol,
                "entry_price": pos["entry_price"],
                "exit_price": entry_price,
                "quantity": pos["quantity"],
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "signal_id": pos["signal_id"],
                "closed_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            self._closed_trades.append(trade)
            self._signals_executed += 1
            return {"executed": True, "action": "SELL", "symbol": symbol, "pnl": pnl}

        return {"executed": False, "reason": "No matching position or direction"}

    def check_stops(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        """현재 가격으로 손절/목표가 도달 여부를 체크한다.

        Returns:
            트리거된 포지션 청산 목록.
        """
        triggered: list[dict[str, Any]] = []
        to_close: list[str] = []

        for symbol, pos in self._positions.items():
            current = prices.get(symbol)
            if current is None:
                continue

            stop_loss = pos.get("stop_loss_price", 0)
            target = pos.get("target_price", 0)

            if stop_loss and current <= stop_loss:
                to_close.append(symbol)
                triggered.append({
                    "symbol": symbol, "trigger": "stop_loss",
                    "price": current,
                })
            elif target and current >= target:
                to_close.append(symbol)
                triggered.append({
                    "symbol": symbol, "trigger": "take_profit",
                    "price": current,
                })

        for symbol in to_close:
            price = prices[symbol]
            self.receive_signal({
                "symbol": symbol,
                "direction": "SELL",
                "entry_price": price,
            })

        return triggered

    def get_performance(self) -> dict[str, Any]:
        """Paper Trading 성과 리포트."""
        winning = [t for t in self._closed_trades if t["pnl"] > 0]
        losing = [t for t in self._closed_trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in self._closed_trades)

        return {
            "initial_balance": self._initial_balance,
            "current_balance": self._balance,
            "unrealized_positions": len(self._positions),
            "total_trades": len(self._closed_trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": len(winning) / len(self._closed_trades) if self._closed_trades else 0.0,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl / self._initial_balance,
            "signals_received": self._signals_received,
            "signals_executed": self._signals_executed,
            "execution_rate": (
                self._signals_executed / self._signals_received
                if self._signals_received > 0
                else 0.0
            ),
        }
