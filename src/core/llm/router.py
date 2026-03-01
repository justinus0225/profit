"""LLM 라우터.

에이전트 이름 → 프로바이더/모델 매핑을 관리한다.
config의 agent_overrides를 참조하여 에이전트별 적절한 LLMClient를 반환한다.
"""

from __future__ import annotations

import logging

from src.core.config import LLMConfig
from src.core.llm.client import LLMClient
from src.core.llm.fallback import FallbackManager
from src.core.llm.providers.base import BaseLLMProvider
from src.core.llm.providers.claude import ClaudeProvider
from src.core.llm.providers.gemini import GeminiProvider
from src.core.llm.providers.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

PROVIDER_MAP: dict[str, type[BaseLLMProvider]] = {
    "claude": ClaudeProvider,
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
}

DEFAULT_MODEL_MAP: dict[str, str] = {
    "claude": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-pro",
    "openai": "gpt-4o",
}


class LLMRouter:
    """에이전트별 LLM 프로바이더/모델 라우팅.

    설정 기반으로 각 에이전트에 적절한 LLMClient를 제공한다.
    폴백 체인을 통해 프로바이더 장애 시 자동 전환을 지원한다.
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._providers: dict[str, BaseLLMProvider] = {}
        self._fallback_managers: dict[str, FallbackManager] = {}

    def _get_or_create_provider(
        self,
        provider_name: str,
        model: str | None = None,
    ) -> BaseLLMProvider:
        """프로바이더 인스턴스를 반환한다 (캐싱)."""
        key = f"{provider_name}:{model or 'default'}"
        if key not in self._providers:
            cls = PROVIDER_MAP.get(provider_name)
            if cls is None:
                raise ValueError(f"Unknown LLM provider: {provider_name}")

            default_model = model or DEFAULT_MODEL_MAP.get(provider_name, "")
            self._providers[key] = cls(
                default_model=default_model,
                retry_config=self._config.retry,
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
            )
        return self._providers[key]

    def get_client(self, agent_name: str) -> FallbackManager:
        """에이전트 이름에 해당하는 LLM 클라이언트를 반환한다.

        agent_overrides에 설정이 있으면 해당 프로바이더/모델을 사용하고,
        없으면 기본 프로바이더/모델을 사용한다.
        폴백 체인이 자동 구성된다.
        """
        if agent_name in self._fallback_managers:
            return self._fallback_managers[agent_name]

        # 에이전트별 오버라이드 확인
        override = self._config.agent_overrides.get(agent_name)
        if override:
            provider_name = override.provider or self._config.default_provider
            model = override.model or self._config.default_model
        else:
            provider_name = self._config.default_provider
            model = self._config.default_model

        # 주 프로바이더
        primary = self._get_or_create_provider(provider_name, model)

        # 폴백 프로바이더
        fallback: BaseLLMProvider | None = None
        if self._config.fallback_provider and self._config.fallback_provider != provider_name:
            fallback = self._get_or_create_provider(
                self._config.fallback_provider,
                self._config.fallback_model,
            )

        manager = FallbackManager(
            primary=primary,
            fallback=fallback,
            config=self._config.fallback,
        )
        self._fallback_managers[agent_name] = manager

        logger.info(
            "LLM routing: %s → %s/%s (fallback: %s/%s)",
            agent_name,
            provider_name,
            model,
            self._config.fallback_provider or "none",
            self._config.fallback_model or "none",
        )

        return manager

    def get_provider_info(self, agent_name: str) -> dict[str, str]:
        """에이전트의 현재 프로바이더/모델 정보를 반환한다."""
        override = self._config.agent_overrides.get(agent_name)
        if override:
            provider = override.provider or self._config.default_provider
            model = override.model or self._config.default_model
        else:
            provider = self._config.default_provider
            model = self._config.default_model

        return {"provider": provider, "model": model}

    def list_agent_mappings(self) -> dict[str, dict[str, str]]:
        """전체 에이전트의 프로바이더/모델 매핑을 반환한다."""
        agents = [
            "orchestrator",
            "analyst_macro",
            "analyst_micro",
            "analyst_sentiment",
            "quant",
            "risk",
            "portfolio",
            "executor",
            "openclaw",
        ]
        return {agent: self.get_provider_info(agent) for agent in agents}
