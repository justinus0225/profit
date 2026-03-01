"""LLM 프로바이더 공통 기반 클래스.

재시도, 에러 핸들링, 지연시간 측정 등 공통 로직을 제공한다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

from src.core.config import LLMRetryConfig
from src.core.llm.client import (
    AnalysisResult,
    LLMClient,
    LLMResponse,
    Message,
    Role,
)

logger = logging.getLogger(__name__)


class LLMProviderError(Exception):
    """LLM 프로바이더 호출 실패."""

    def __init__(self, provider: str, message: str, retryable: bool = True) -> None:
        self.provider = provider
        self.retryable = retryable
        super().__init__(f"[{provider}] {message}")


class BaseLLMProvider(LLMClient):
    """LLM 프로바이더 공통 구현.

    각 프로바이더(Claude, Gemini, OpenAI)는 이 클래스를 상속하고
    _do_chat(), _do_stream() 만 구현하면 된다.
    """

    def __init__(
        self,
        default_model: str,
        retry_config: LLMRetryConfig | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> None:
        self._default_model = default_model
        self._retry = retry_config or LLMRetryConfig()
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        resolved_model = model or self._default_model
        resolved_temp = temperature if temperature is not None else self._temperature
        resolved_max = max_tokens or self._max_tokens

        return await self._with_retry(
            self._do_chat,
            messages,
            model=resolved_model,
            temperature=resolved_temp,
            max_tokens=resolved_max,
        )

    async def analyze(
        self,
        prompt: str,
        context: str = "",
        *,
        model: str | None = None,
    ) -> AnalysisResult:
        messages = []
        if context:
            messages.append(Message(role=Role.SYSTEM, content=context))
        messages.append(Message(role=Role.USER, content=prompt))

        response = await self.chat(messages, model=model)
        return AnalysisResult(
            content=response.content,
            response=response,
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        resolved_model = model or self._default_model
        resolved_temp = temperature if temperature is not None else self._temperature
        resolved_max = max_tokens or self._max_tokens

        async for chunk in self._do_stream(
            messages,
            model=resolved_model,
            temperature=resolved_temp,
            max_tokens=resolved_max,
        ):
            yield chunk

    async def _with_retry(self, fn, *args, **kwargs) -> LLMResponse:  # noqa: ANN002, ANN003
        last_error: Exception | None = None
        delay = self._retry.initial_delay_seconds

        for attempt in range(self._retry.max_retries + 1):
            try:
                start = time.monotonic()
                result = await fn(*args, **kwargs)
                result.latency_ms = (time.monotonic() - start) * 1000
                return result
            except LLMProviderError as e:
                last_error = e
                if not e.retryable or attempt >= self._retry.max_retries:
                    raise
                logger.warning(
                    "%s (attempt %d/%d, retry in %.1fs)",
                    e,
                    attempt + 1,
                    self._retry.max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
                if self._retry.backoff == "exponential":
                    delay *= 2
            except Exception as e:
                last_error = e
                if attempt >= self._retry.max_retries:
                    raise LLMProviderError(
                        self.provider_name, str(e), retryable=False
                    ) from e
                logger.warning(
                    "Unexpected error: %s (attempt %d/%d)",
                    e,
                    attempt + 1,
                    self._retry.max_retries,
                )
                await asyncio.sleep(delay)
                if self._retry.backoff == "exponential":
                    delay *= 2

        raise last_error or LLMProviderError(self.provider_name, "Max retries exceeded")

    # --- 서브클래스가 구현할 메서드 ---

    async def _do_chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        raise NotImplementedError

    async def _do_stream(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        raise NotImplementedError
        yield  # type: ignore[misc]  # make it an async generator
