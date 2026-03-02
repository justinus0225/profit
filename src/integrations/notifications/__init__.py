"""알림 통합 모듈.

Telegram / Discord 웹훅을 통한 관리자 알림 발송.
"""

from src.integrations.notifications.dispatcher import NotificationDispatcher

__all__ = ["NotificationDispatcher"]
