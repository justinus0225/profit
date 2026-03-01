"""OpenAI LLM 프로바이더."""

from __future__ import annotations

import os
from typing import AsyncIterator

import openai

from src.core.config import LLMRetryConfig
from src.core.llm.client import EmbeddingResult, LLMResponse, Message, Role
from src.core.llm.providers.base import BaseLLMProvider, LLMProviderError


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API 기반 LLM 프로바이더."""

    _default_embed_model: str = "text-embedding-3-small"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "gpt-4o",
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
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._client = openai.AsyncOpenAI(api_key=key)

    @property
    def provider_name(self) -> str:
        return "openai"

    async def _do_chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        api_messages = []
        for msg in messages:
            api_messages.append({"role": msg.role.value, "content": msg.content})

        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = response.choices[0].message.content or ""
            input_tokens = response.usage.prompt_tokens if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0

            return LLMResponse(
                content=content,
                model=model,
                provider=self.provider_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except openai.RateLimitError as e:
            raise LLMProviderError(self.provider_name, f"Rate limited: {e}", retryable=True) from e
        except openai.APIError as e:
            raise LLMProviderError(self.provider_name, str(e), retryable=True) from e

    async def _do_stream(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        api_messages = []
        for msg in messages:
            api_messages.append({"role": msg.role.value, "content": msg.content})

        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except openai.APIError as e:
            raise LLMProviderError(self.provider_name, str(e), retryable=True) from e

    async def _do_embed(self, text: str, *, model: str) -> EmbeddingResult:
        try:
            response = await self._client.embeddings.create(
                model=model,
                input=text,
            )
            vector = response.data[0].embedding
            return EmbeddingResult(
                vector=vector,
                model=model,
                provider=self.provider_name,
                dimensions=len(vector),
                input_tokens=response.usage.prompt_tokens if response.usage else 0,
            )
        except openai.APIError as e:
            raise LLMProviderError(self.provider_name, str(e), retryable=True) from e

    async def health_check(self) -> bool:
        try:
            await self._client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False
