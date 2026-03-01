"""에이전트 기본 추상 클래스.

모든 에이전트(8개 + 오케스트레이터)가 이 클래스를 상속한다.
LLM 호출, Redis pub/sub, 설정 접근, 헬스체크 등 공통 기능을 제공한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Coroutine

import redis.asyncio as aioredis

from src.core.config import ProfitConfig
from src.core.llm.client import LLMResponse, Message
from src.core.llm.router import LLMRouter

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    WARMING = "warming"
    STOPPED = "stopped"
    ERROR = "error"


class BaseAgent(ABC):
    """에이전트 기본 추상 클래스.

    모든 에이전트는 이 클래스를 상속하고 다음을 구현해야 한다:
    - agent_type: 에이전트 유형 (예: "quant", "risk")
    - _on_initialize(): 초기화 로직
    - _on_run(): 메인 실행 로직
    """

    def __init__(
        self,
        name: str,
        config: ProfitConfig,
        llm_router: LLMRouter,
        redis_client: aioredis.Redis,
    ) -> None:
        self.name = name
        self._config = config
        self._llm_router = llm_router
        self._redis = redis_client
        self._status = AgentStatus.INITIALIZING
        self._started_at: float | None = None
        self._last_heartbeat: float = 0.0
        self._running = False
        self._subscriptions: dict[str, asyncio.Task[None]] = {}

    @property
    @abstractmethod
    def agent_type(self) -> str:
        """에이전트 유형 식별자 (예: 'quant', 'risk', 'orchestrator')."""

    @property
    def status(self) -> AgentStatus:
        return self._status

    @property
    def config(self) -> ProfitConfig:
        return self._config

    # ── 생명주기 관리 ──

    async def initialize(self) -> None:
        """에이전트 초기화. 서브클래스의 _on_initialize()를 호출한다."""
        logger.info("[%s] Initializing...", self.name)
        self._status = AgentStatus.INITIALIZING
        await self._on_initialize()
        self._status = AgentStatus.READY
        logger.info("[%s] Ready", self.name)

    async def run(self) -> None:
        """에이전트 메인 루프 시작. 서브클래스의 _on_run()을 호출한다."""
        self._running = True
        self._started_at = time.time()
        self._status = AgentStatus.RUNNING
        logger.info("[%s] Running", self.name)

        try:
            await self._on_run()
        except asyncio.CancelledError:
            logger.info("[%s] Cancelled", self.name)
        except Exception:
            self._status = AgentStatus.ERROR
            logger.exception("[%s] Error in run loop", self.name)
            raise
        finally:
            self._running = False

    async def stop(self) -> None:
        """에이전트 정지."""
        logger.info("[%s] Stopping...", self.name)
        self._running = False

        # 구독 태스크 취소
        for channel, task in self._subscriptions.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._subscriptions.clear()

        await self._on_stop()
        self._status = AgentStatus.STOPPED
        logger.info("[%s] Stopped", self.name)

    # ── LLM 호출 ──

    async def _llm_chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """LLM 호출 (LLMRouter 경유, 폴백 포함)."""
        client = self._llm_router.get_client(self.agent_type)
        return await client.chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # ── Redis Pub/Sub ──

    async def _publish(self, channel: str, message: dict[str, Any]) -> None:
        """Redis 채널에 메시지를 발행한다."""
        payload = json.dumps(message, ensure_ascii=False, default=str)
        await self._redis.publish(channel, payload)

    async def _subscribe(
        self,
        channel: str,
        callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        """Redis 채널을 구독하고 메시지 수신 시 콜백을 호출한다."""

        async def _listener() -> None:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(channel)
            try:
                async for msg in pubsub.listen():
                    if msg["type"] == "message":
                        data = json.loads(msg["data"])
                        await callback(data)
            except asyncio.CancelledError:
                await pubsub.unsubscribe(channel)
                await pubsub.close()

        task = asyncio.create_task(_listener())
        self._subscriptions[channel] = task

    # ── 헬스체크 ──

    async def heartbeat(self) -> dict[str, Any]:
        """에이전트 상태를 Redis에 기록하고 pub/sub으로 브로드캐스트한다."""
        now = time.time()
        self._last_heartbeat = now

        info = {
            "agent": self.name,
            "type": self.agent_type,
            "status": self._status.value,
            "timestamp": now,
            "uptime_seconds": now - self._started_at if self._started_at else 0,
        }

        payload = json.dumps(info, default=str)
        await self._redis.hset("agent:heartbeat", self.agent_type, payload)
        await self._redis.publish("agent:status_changed", payload)
        return info

    # ── 서브클래스 구현 ──

    async def _on_initialize(self) -> None:
        """서브클래스별 초기화 로직 (오버라이드 선택)."""

    @abstractmethod
    async def _on_run(self) -> None:
        """서브클래스별 메인 실행 로직 (오버라이드 필수)."""

    async def _on_stop(self) -> None:
        """서브클래스별 정지 로직 (오버라이드 선택)."""
