"""LLM 클라이언트 추상화 단위 테스트."""

from __future__ import annotations

import pytest

from src.core.llm.client import (
    AnalysisResult,
    EmbeddingResult,
    LLMResponse,
    Message,
    Role,
)


class TestMessage:
    def test_message_creation(self) -> None:
        msg = Message(Role.SYSTEM, "Hello")
        assert msg.role == Role.SYSTEM
        assert msg.content == "Hello"

    def test_message_immutable(self) -> None:
        msg = Message(Role.USER, "Test")
        with pytest.raises(AttributeError):
            msg.content = "Changed"  # type: ignore[misc]


class TestLLMResponse:
    def test_total_tokens(self) -> None:
        resp = LLMResponse(
            content="Test",
            model="test-model",
            provider="test",
            input_tokens=10,
            output_tokens=5,
        )
        assert resp.total_tokens == 15

    def test_defaults(self) -> None:
        resp = LLMResponse(content="Test", model="m", provider="p")
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0
        assert resp.total_tokens == 0


class TestEmbeddingResult:
    def test_embedding_creation(self) -> None:
        result = EmbeddingResult(
            vector=[0.1, 0.2, 0.3],
            model="embed-model",
            provider="test",
            dimensions=3,
        )
        assert len(result.vector) == 3
        assert result.dimensions == 3


class TestAnalysisResult:
    def test_analysis_creation(self) -> None:
        result = AnalysisResult(
            content="BTC is bullish", confidence=0.85
        )
        assert result.confidence == 0.85
        assert result.response is None


class TestFakeLLMClient:
    @pytest.mark.asyncio
    async def test_chat(self, fake_llm) -> None:
        response = await fake_llm.chat([Message(Role.USER, "Hello")])
        assert response.content == "Test response"
        assert response.provider == "fake"

    @pytest.mark.asyncio
    async def test_embed(self, fake_llm) -> None:
        result = await fake_llm.embed("Test text")
        assert len(result.vector) == 768
        assert result.provider == "fake"

    @pytest.mark.asyncio
    async def test_analyze(self, fake_llm) -> None:
        result = await fake_llm.analyze("Analyze this")
        assert result.confidence == 0.8

    @pytest.mark.asyncio
    async def test_health_check(self, fake_llm) -> None:
        assert await fake_llm.health_check() is True

    def test_provider_name(self, fake_llm) -> None:
        assert fake_llm.provider_name == "fake"
