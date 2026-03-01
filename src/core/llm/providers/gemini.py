"""Google Gemini LLM 프로바이더."""

from __future__ import annotations

import os
from typing import AsyncIterator

from google import genai
from google.genai import types

from src.core.config import LLMRetryConfig
from src.core.llm.client import LLMResponse, Message, Role
from src.core.llm.providers.base import BaseLLMProvider, LLMProviderError


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API 기반 LLM 프로바이더."""

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "gemini-2.5-pro",
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
        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._client = genai.Client(api_key=key)

    @property
    def provider_name(self) -> str:
        return "gemini"

    def _build_contents(self, messages: list[Message]) -> tuple[str | None, list[types.Content]]:
        """Message 리스트를 Gemini API 형식으로 변환."""
        system_instruction: str | None = None
        contents: list[types.Content] = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_instruction = msg.content
            else:
                role = "user" if msg.role == Role.USER else "model"
                contents.append(
                    types.Content(role=role, parts=[types.Part(text=msg.content)])
                )

        return system_instruction, contents

    async def _do_chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        system_instruction, contents = self._build_contents(messages)

        try:
            config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
                system_instruction=system_instruction,
            )
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )

            content = response.text or ""
            input_tokens = 0
            output_tokens = 0
            if response.usage_metadata:
                input_tokens = response.usage_metadata.prompt_token_count or 0
                output_tokens = response.usage_metadata.candidates_token_count or 0

            return LLMResponse(
                content=content,
                model=model,
                provider=self.provider_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except Exception as e:
            raise LLMProviderError(self.provider_name, str(e), retryable=True) from e

    async def _do_stream(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        system_instruction, contents = self._build_contents(messages)

        try:
            config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
                system_instruction=system_instruction,
            )
            async for chunk in self._client.aio.models.generate_content_stream(
                model=model,
                contents=contents,
                config=config,
            ):
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            raise LLMProviderError(self.provider_name, str(e), retryable=True) from e

    async def health_check(self) -> bool:
        try:
            await self._client.aio.models.generate_content(
                model="gemini-2.0-flash-lite",
                contents="ping",
            )
            return True
        except Exception:
            return False
