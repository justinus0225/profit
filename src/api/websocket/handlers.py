"""WebSocket 엔드포인트 핸들러.

통합 WebSocket 엔드포인트: /ws
클라이언트가 subscribe 메시지로 채널을 선택한다.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.api.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """통합 WebSocket 엔드포인트.

    클라이언트 → 서버:
        {"type": "subscribe", "channels": ["signals", "orders"]}
        {"type": "unsubscribe", "channels": ["signals"]}
        {"type": "ping", "id": 1}

    서버 → 클라이언트:
        {"type": "subscription_confirmed", "channels": [...]}
        {"type": "pong", "id": 1}
        {... 채널별 이벤트 데이터 ...}
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
                await ws_manager.handle_client_message(websocket, data)
            except json.JSONDecodeError:
                await ws_manager.send_personal(websocket, {
                    "type": "error",
                    "message": "Invalid JSON",
                })
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
