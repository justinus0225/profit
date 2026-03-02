"""LLM 메모리 관리 단위 테스트 (P11)."""

from __future__ import annotations

import pytest

from src.core.config import LLMMemoryConfig
from src.core.llm.context import ContextBuildResult, ContextManager
from src.core.llm.memory import AgentMemoryManager, MemoryEntry
from src.core.llm.rag import RAGPipeline, RAGResult, RAGSearchResult, _cosine_similarity


@pytest.fixture
def memory_config() -> LLMMemoryConfig:
    return LLMMemoryConfig()


# ── AgentMemoryManager 테스트 ──


class TestAgentMemoryManager:
    @pytest.mark.asyncio
    async def test_store_and_get(self, fake_redis, memory_config) -> None:
        mgr = AgentMemoryManager(fake_redis, memory_config)
        await mgr.store_short_term("quant", "key1", "test content")
        entry = await mgr.get_short_term("quant", "key1")
        assert entry is not None
        assert entry.content == "test content"
        assert entry.key == "key1"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, fake_redis, memory_config) -> None:
        mgr = AgentMemoryManager(fake_redis, memory_config)
        entry = await mgr.get_short_term("quant", "nonexistent")
        assert entry is None

    @pytest.mark.asyncio
    async def test_delete(self, fake_redis, memory_config) -> None:
        mgr = AgentMemoryManager(fake_redis, memory_config)
        await mgr.store_short_term("quant", "to_delete", "temp")
        await mgr.delete_short_term("quant", "to_delete")
        entry = await mgr.get_short_term("quant", "to_delete")
        assert entry is None

    @pytest.mark.asyncio
    async def test_clear(self, fake_redis, memory_config) -> None:
        mgr = AgentMemoryManager(fake_redis, memory_config)
        await mgr.store_short_term("quant", "k1", "v1")
        await mgr.store_short_term("quant", "k2", "v2")
        await mgr.clear_short_term("quant")
        stats = await mgr.get_memory_stats("quant")
        assert stats["short_term_count"] == 0

    @pytest.mark.asyncio
    async def test_get_recent(self, fake_redis, memory_config) -> None:
        mgr = AgentMemoryManager(fake_redis, memory_config)
        await mgr.store_short_term("analyst", "m1", "content1")
        await mgr.store_short_term("analyst", "m2", "content2")
        await mgr.store_short_term("analyst", "m3", "content3")

        recent = await mgr.get_recent_short_term("analyst", limit=2)
        assert len(recent) == 2


# ── ContextManager 테스트 ──


class TestContextManager:
    @pytest.mark.asyncio
    async def test_build_basic_context(
        self, fake_llm, memory_config
    ) -> None:
        ctx = ContextManager(memory_config, compression_client=fake_llm)
        result = await ctx.build_context(
            agent_type="quant",
            system_prompt="You are a quant agent.",
            task_content="Analyze BTC/KRW",
        )
        assert isinstance(result, ContextBuildResult)
        assert len(result.messages) >= 1
        assert result.total_tokens > 0
        assert "system" in result.blocks_used

    @pytest.mark.asyncio
    async def test_context_with_memories(
        self, fake_llm, memory_config
    ) -> None:
        ctx = ContextManager(memory_config, compression_client=fake_llm)
        result = await ctx.build_context(
            agent_type="analyst",
            system_prompt="You are an analyst.",
            task_content="Market analysis",
            short_term_memories=["Previous trade was profitable"],
            rag_results=["[decision] BTC rally detected"],
        )
        assert "short_term" in result.blocks_used or "rag" in result.blocks_used

    def test_get_max_tokens(self, memory_config) -> None:
        ctx = ContextManager(memory_config)
        tokens = ctx.get_max_tokens("quant")
        assert tokens > 0


# ── RAG 유틸리티 테스트 ──


class TestRAGUtilities:
    def test_cosine_similarity_identical(self) -> None:
        vec = [1.0, 0.0, 1.0]
        assert _cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal(self) -> None:
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine_similarity(a, b) == pytest.approx(0.0)

    def test_cosine_similarity_opposite(self) -> None:
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_cosine_similarity_different_lengths(self) -> None:
        assert _cosine_similarity([1.0], [1.0, 2.0]) == 0.0

    def test_cosine_similarity_zero_vector(self) -> None:
        assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestRAGFormatResults:
    def test_format_empty(self, fake_llm, memory_config) -> None:
        rag = RAGPipeline(fake_llm, memory_config)
        result = RAGSearchResult(query="test", results=[])
        formatted = rag.format_results(result)
        assert formatted == []

    def test_format_with_results(self, fake_llm, memory_config) -> None:
        from datetime import datetime, timezone

        rag = RAGPipeline(fake_llm, memory_config)
        result = RAGSearchResult(
            query="test",
            results=[
                RAGResult(
                    content="BTC was bullish",
                    similarity=0.85,
                    agent_type="quant",
                    memory_type="decision",
                    created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                )
            ],
        )
        formatted = rag.format_results(result)
        assert len(formatted) == 1
        assert "BTC was bullish" in formatted[0]
        assert "0.85" in formatted[0]
