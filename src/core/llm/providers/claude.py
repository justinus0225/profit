"""Anthropic Claude LLM 프로바이더."""

from __future__ import annotations

import os
from typing import AsyncIterator

import anthropic

from src.core.config import LLMRetryConfig
from src.core.llm.client import EmbeddingResult, LLMResponse, Message, Role
from src.core.llm.providers.base import BaseLLMProvider, LLMProviderError


class ClaudeProvider(BaseLLMProvider):
    """Anthropic Claude API 기반 LLM 프로바이더."""

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "claude-sonnet-4-6",
        retry_config: LLMRetryConfig | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> None:
        super().__init__(
            default_model=default_model,
            retry_config=retry_config,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        key = api_key or os.environ.get("CLAUDE_API_KEY", "")
        self._client = anthropic.AsyncAnthropic(api_key=key)

    @property
    def provider_name(self) -> str:
        return "claude"

    async def _do_chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        system_prompt = ""
        api_messages = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_prompt = msg.content
            else:
                api_messages.append({"role": msg.role.value, "content": msg.content})

        try:
            kwargs: dict = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": api_messages,
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            response = await self._client.messages.create(**kwargs)

            content = ""
            for block in response.content:
                if block.type == "text":
                    content += block.text

            return LLMResponse(
                content=content,
                model=model,
                provider=self.provider_name,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
        except anthropic.RateLimitError as e:
            raise LLMProviderError(self.provider_name, f"Rate limited: {e}", retryable=True) from e
        except anthropic.APIError as e:
            raise LLMProviderError(self.provider_name, str(e), retryable=True) from e

    async def _do_stream(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        system_prompt = ""
        api_messages = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_prompt = msg.content
            else:
                api_messages.append({"role": msg.role.value, "content": msg.content})

        try:
            kwargs: dict = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": api_messages,
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
        except anthropic.APIError as e:
            raise LLMProviderError(self.provider_name, str(e), retryable=True) from e

    async def health_check(self) -> bool:
        try:
            await self._client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False
