"""LLM 폴백 체인 관리.

주 프로바이더 연속 실패 감지 → 폴백 프로바이더 자동 전환 → 정상화 시 자동 복귀.
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.core.config import LLMFallbackConfig
from src.core.llm.client import LLMClient, LLMResponse, Message
from src.core.llm.providers.base import LLMProviderError

logger = logging.getLogger(__name__)


class FallbackManager:
    """프로바이더 폴백 체인 관리자.

    연속 실패 횟수를 추적하고, 임계값 초과 시 폴백 프로바이더로 전환한다.
    주 프로바이더 정상화 확인을 주기적으로 수행하여 자동 복귀한다.
    """

    def __init__(
        self,
        primary: LLMClient,
        fallback: LLMClient | None = None,
        config: LLMFallbackConfig | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._config = config or LLMFallbackConfig()
        self._consecutive_failures = 0
        self._using_fallback = False
        self._fallback_since: float | None = None
        self._last_recovery_check: float = 0.0

    @property
    def is_using_fallback(self) -> bool:
        return self._using_fallback

    @property
    def active_provider(self) -> LLMClient:
        if self._using_fallback and self._fallback:
            return self._fallback
        return self._primary

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """폴백 체인을 포함한 LLM 호출."""
        # 자동 복구 확인
        if self._using_fallback and self._config.auto_recover:
            await self._check_recovery()

        try:
            response = await self.active_provider.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            self._on_success()
            return response
        except LLMProviderError:
            self._on_failure()
            if self._should_fallback():
                return await self._try_fallback(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            raise

    def _on_success(self) -> None:
        self._consecutive_failures = 0

    def _on_failure(self) -> None:
        self._consecutive_failures += 1
        logger.warning(
            "Provider failure %d/%d",
            self._consecutive_failures,
            self._config.consecutive_failures,
        )

    def _should_fallback(self) -> bool:
        return (
            not self._using_fallback
            and self._fallback is not None
            and self._consecutive_failures >= self._config.consecutive_failures
        )

    async def _try_fallback(
        self,
        messages: list[Message],
        **kwargs: object,
    ) -> LLMResponse:
        if not self._fallback:
            raise LLMProviderError("fallback", "No fallback provider configured", retryable=False)

        logger.warning(
            "Switching to fallback provider: %s",
            self._fallback.provider_name,
        )
        self._using_fallback = True
        self._fallback_since = time.time()
        self._consecutive_failures = 0

        return await self._fallback.chat(messages, **kwargs)  # type: ignore[arg-type]

    async def _check_recovery(self) -> None:
        now = time.time()
        check_interval = self._config.recovery_check_minutes * 60
        if now - self._last_recovery_check < check_interval:
            return

        self._last_recovery_check = now
        logger.info("Checking primary provider recovery...")

        if await self._primary.health_check():
            logger.info(
                "Primary provider recovered after %.0fs",
                now - (self._fallback_since or now),
            )
            self._using_fallback = False
            self._fallback_since = None
            self._consecutive_failures = 0

    async def start_recovery_loop(self) -> None:
        """백그라운드 복구 확인 루프 (선택적)."""
        while True:
            if self._using_fallback and self._config.auto_recover:
                await self._check_recovery()
            await asyncio.sleep(self._config.recovery_check_minutes * 60)
