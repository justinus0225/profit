"""OpenClaw 메시지 핸들러.

관리자가 OpenClaw 메시지를 보내면 명령 파싱 → 에이전트 라우팅 → 응답 취합.
FastAPI 엔드포인트(/api/openclaw/webhook)에서 호출된다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.integrations.openclaw.commands import COMMAND_REGISTRY, parse_command

logger = logging.getLogger(__name__)


class OpenClawHandler:
    """OpenClaw 메시지 수신/응답 처리."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """OpenClaw 메시지를 처리하고 응답을 반환한다.

        Args:
            message: {"text": str, "user_id": str, "channel": str}

        Returns:
            {"response": str, "command": str, "success": bool}
        """
        text = message.get("text", "").strip()
        user_id = message.get("user_id", "unknown")

        logger.info("OpenClaw message from %s: %s", user_id, text[:100])

        cmd, args = parse_command(text)

        if cmd not in COMMAND_REGISTRY:
            return {
                "response": f"Unknown command: {cmd}. Use /help for available commands.",
                "command": cmd,
                "success": False,
            }

        handler_fn = COMMAND_REGISTRY[cmd]
        try:
            result = await handler_fn(self._redis, args)
            return {
                "response": result,
                "command": cmd,
                "success": True,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
        except Exception:
            logger.exception("Command error: %s", cmd)
            return {
                "response": f"Error executing /{cmd}.",
                "command": cmd,
                "success": False,
            }
