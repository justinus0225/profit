"""LLM 클라이언트 추상 인터페이스 및 데이터 모델.

모든 에이전트는 이 인터페이스만 사용하며,
프로바이더 교체 시 에이전트 코드 수정이 불필요하다.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class EmbeddingResult:
    """텍스트 임베딩 결과 (RAG/pgvector 용)."""
    vector: list[float]
    model: str
    provider: str
    dimensions: int = 0
    input_tokens: int = 0
    latency_ms: float = 0.0


@dataclass
class AnalysisResult:
    """에이전트 분석 결과."""
    content: str
    confidence: float = 0.0  # 0.0 ~ 1.0
    metadata: dict[str, object] = field(default_factory=dict)
    response: LLMResponse | None = None


class LLMClient(ABC):
    """LLM 프로바이더 추상 인터페이스.

    모든 프로바이더(Claude, Gemini, OpenAI)가 이 인터페이스를 구현한다.
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """메시지 기반 대화 완성."""

    @abstractmethod
    async def analyze(
        self,
        prompt: str,
        context: str = "",
        *,
        model: str | None = None,
    ) -> AnalysisResult:
        """단일 프롬프트 기반 분석 (에이전트 편의 메서드)."""

    @abstractmethod
    async def embed(
        self,
        text: str,
        *,
        model: str | None = None,
    ) -> EmbeddingResult:
        """텍스트 → 벡터 임베딩 (RAG/pgvector 파이프라인 용)."""

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """스트리밍 응답."""

    @abstractmethod
    async def health_check(self) -> bool:
        """프로바이더 연결 상태 확인."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """프로바이더 이름 (예: 'claude', 'gemini', 'openai')."""
