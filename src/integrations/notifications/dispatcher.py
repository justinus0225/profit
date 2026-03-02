"""알림 발송 디스패처.

Telegram Bot API / Discord Webhook을 통해 관리자에게 알림을 전송한다.
환경 변수:
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — Telegram 알림
  DISCORD_WEBHOOK_URL — Discord 알림
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class NotificationDispatcher:
    """멀티 채널 알림 디스패처."""

    def __init__(self) -> None:
        self._telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self._discord_webhook = os.getenv("DISCORD_WEBHOOK_URL", "")

    @property
    def telegram_enabled(self) -> bool:
        return bool(self._telegram_token and self._telegram_chat_id)

    @property
    def discord_enabled(self) -> bool:
        return bool(self._discord_webhook)

    async def send(
        self, title: str, message: str, severity: str = "INFO"
    ) -> dict[str, Any]:
        """모든 활성 채널로 알림을 전송한다."""
        results: dict[str, Any] = {}
        text = f"[{severity}] {title}\n{message}"

        if self.telegram_enabled:
            results["telegram"] = await self._send_telegram(text)
        if self.discord_enabled:
            results["discord"] = await self._send_discord(text)

        if not results:
            logger.debug("No notification channels configured")

        return results

    async def send_alert(
        self, alert_type: str, data: dict[str, Any]
    ) -> None:
        """시스템 알림 발송 (리스크 이벤트, 서킷 브레이커 등)."""
        severity_map = {
            "circuit_breaker": "CRITICAL",
            "stop_loss": "WARNING",
            "daily_loss_limit": "CRITICAL",
            "agent_error": "WARNING",
            "trade_filled": "INFO",
        }
        severity = severity_map.get(alert_type, "INFO")
        title = f"P.R.O.F.I.T. Alert: {alert_type}"
        message = "\n".join(f"  {k}: {v}" for k, v in data.items())
        await self.send(title, message, severity)

    async def _send_telegram(self, text: str) -> bool:
        """Telegram Bot API로 메시지 전송."""
        try:
            url = _TELEGRAM_API.format(token=self._telegram_token)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json={
                    "chat_id": self._telegram_chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                })
                resp.raise_for_status()
            logger.info("Telegram notification sent")
            return True
        except Exception:
            logger.warning("Telegram send failed", exc_info=True)
            return False

    async def _send_discord(self, text: str) -> bool:
        """Discord Webhook으로 메시지 전송."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self._discord_webhook,
                    json={"content": text},
                )
                resp.raise_for_status()
            logger.info("Discord notification sent")
            return True
        except Exception:
            logger.warning("Discord send failed", exc_info=True)
            return False
