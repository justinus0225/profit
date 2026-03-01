"""WebSocket 커넥션 매니저 - 실시간 스트리밍.

ARCHITECTURE.md: 실시간 WebSocket 채널.
채널 종류:
- prices: 실시간 가격
- signals: 시그널 생성
- consensus: 합의 결정
- orders: 주문 상태
- agents: 에이전트 이벤트
- portfolio: 포트폴리오 업데이트
- alerts: 시스템 알림
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """WebSocket 연결 관리자.

    채널별 구독 관리 및 브로드캐스트를 담당한다.
    Redis pub/sub을 통해 에이전트 이벤트를 수신하여 클라이언트에 전달.
    """

    def __init__(self) -> None:
        # channel → set of websocket connections
        self._subscriptions: dict[str, set[WebSocket]] = {}
        self._active_connections: set[WebSocket] = set()

    @property
    def connection_count(self) -> int:
        return len(self._active_connections)

    async def connect(self, websocket: WebSocket) -> None:
        """WebSocket 연결 수락."""
        await websocket.accept()
        self._active_connections.add(websocket)
        logger.info("WebSocket connected (total=%d)", self.connection_count)

    def disconnect(self, websocket: WebSocket) -> None:
        """WebSocket 연결 해제."""
        self._active_connections.discard(websocket)
        for channel, subs in self._subscriptions.items():
            subs.discard(websocket)
        logger.info("WebSocket disconnected (total=%d)", self.connection_count)

    def subscribe(self, websocket: WebSocket, channel: str) -> None:
        """채널 구독 등록."""
        if channel not in self._subscriptions:
            self._subscriptions[channel] = set()
        self._subscriptions[channel].add(websocket)

    def unsubscribe(self, websocket: WebSocket, channel: str) -> None:
        """채널 구독 해제."""
        if channel in self._subscriptions:
            self._subscriptions[channel].discard(websocket)

    async def send_personal(self, websocket: WebSocket, message: dict[str, Any]) -> None:
        """특정 클라이언트에 메시지 전송."""
        try:
            await websocket.send_json(message)
        except Exception:
            self.disconnect(websocket)

    async def broadcast(self, channel: str, message: dict[str, Any]) -> None:
        """채널 구독자 전체에 메시지 브로드캐스트."""
        subscribers = self._subscriptions.get(channel, set())
        if not subscribers:
            return

        disconnected: list[WebSocket] = []
        for ws in subscribers:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(ws)

    async def broadcast_all(self, message: dict[str, Any]) -> None:
        """모든 연결된 클라이언트에 메시지 전송."""
        disconnected: list[WebSocket] = []
        for ws in self._active_connections:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(ws)

    async def handle_client_message(
        self, websocket: WebSocket, data: dict[str, Any]
    ) -> None:
        """클라이언트 메시지 처리 (subscribe/unsubscribe/ping)."""
        msg_type = data.get("type", "")

        if msg_type == "subscribe":
            channels = data.get("channels", [])
            for ch in channels:
                self.subscribe(websocket, ch)
            await self.send_personal(websocket, {
                "type": "subscription_confirmed",
                "channels": channels,
            })

        elif msg_type == "unsubscribe":
            channels = data.get("channels", [])
            for ch in channels:
                self.unsubscribe(websocket, ch)

        elif msg_type == "ping":
            await self.send_personal(websocket, {
                "type": "pong",
                "id": data.get("id"),
            })


# 전역 싱글턴
ws_manager = ConnectionManager()


class RedisBridge:
    """Redis pub/sub → WebSocket 브리지.

    에이전트들이 Redis에 발행한 이벤트를 감시하여
    WebSocket 클라이언트에 실시간 전달한다.
    """

    # Redis 채널 → WebSocket 채널 매핑
    CHANNEL_MAP: dict[str, str] = {
        "quant:signal": "signals",
        "orchestrator:approval": "consensus",
        "orchestrator:rejection": "consensus",
        "executor:order_created": "orders",
        "executor:order_filled": "orders",
        "executor:order_cancelled": "orders",
        "portfolio:performance_report": "portfolio",
        "portfolio:trade_approved": "orders",
        "risk:stop_loss_triggered": "alerts",
        "risk:trailing_stop_triggered": "alerts",
        "risk:level_changed": "alerts",
        "agent:status_changed": "agents",
        "analyst:market_report": "agents",
        "analyst:watchlist_updated": "agents",
        "system:trading_toggle": "alerts",
        "system:config_changed": "alerts",
        "boot:completed": "alerts",
    }

    def __init__(self, manager: ConnectionManager) -> None:
        self._manager = manager
        self._running = False

    async def start(self, redis_client: Any) -> None:
        """Redis 구독 시작."""
        self._running = True
        pubsub = redis_client.pubsub()

        channels = list(self.CHANNEL_MAP.keys())
        await pubsub.subscribe(*channels)
        logger.info("RedisBridge: subscribed to %d channels", len(channels))

        try:
            async for msg in pubsub.listen():
                if not self._running:
                    break
                if msg["type"] != "message":
                    continue

                redis_channel = msg["channel"]
                if isinstance(redis_channel, bytes):
                    redis_channel = redis_channel.decode()

                ws_channel = self.CHANNEL_MAP.get(redis_channel)
                if not ws_channel:
                    continue

                try:
                    payload = json.loads(msg["data"])
                    payload["_source_channel"] = redis_channel
                    await self._manager.broadcast(ws_channel, payload)
                except json.JSONDecodeError:
                    continue

        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(*channels)
            await pubsub.close()

    def stop(self) -> None:
        self._running = False
