"""뉴스/소셜 감성 분석 모듈.

CoinGecko trending + CryptoCompare 뉴스 API를 활용하여
시장 감성 점수를 산출한다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from src.core.llm.client import LLMResponse, Message, Role

logger = logging.getLogger(__name__)

LLMChatFn = Callable[[list[Message]], Awaitable[LLMResponse]]

# CoinGecko 무료 API (키 불필요)
_COINGECKO_TRENDING = "https://api.coingecko.com/api/v3/search/trending"

# CryptoCompare 무료 뉴스 API (키 불필요)
_CRYPTOCOMPARE_NEWS = "https://min-api.cryptocompare.com/data/v2/news/"


class SentimentAnalyzer:
    """뉴스/소셜 감성 분석."""

    def __init__(self) -> None:
        self.sentiment_scores: dict[str, float] = {}
        self._trending_coins: list[dict[str, Any]] = []
        self._recent_news: list[dict[str, Any]] = []

    async def crawl(self, llm_chat: LLMChatFn | None = None) -> dict[str, Any]:
        """뉴스 크롤링 및 감성 점수 산출.

        Args:
            llm_chat: LLM 호출 함수 (제공 시 뉴스 감성 분석 수행).

        Returns:
            타임스탬프, 트렌딩 코인, 뉴스 요약, 감성 점수를 담은 dict.
        """
        logger.info("News/sentiment crawl started")

        # 1) CoinGecko 트렌딩 코인 조회
        trending = await self._fetch_trending()

        # 2) CryptoCompare 최근 뉴스 조회
        news = await self._fetch_news()

        # 3) LLM 기반 뉴스 감성 분석
        if llm_chat and news:
            await self._analyze_sentiment(news, llm_chat)

        return {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "trending_coins": trending,
            "news_count": len(news),
            "sentiment_scores": dict(self.sentiment_scores),
        }

    async def _fetch_trending(self) -> list[dict[str, Any]]:
        """CoinGecko 트렌딩 코인을 조회한다."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(_COINGECKO_TRENDING)
                resp.raise_for_status()
                data = resp.json()

            coins = data.get("coins", [])
            self._trending_coins = [
                {
                    "name": c.get("item", {}).get("name"),
                    "symbol": c.get("item", {}).get("symbol"),
                    "market_cap_rank": c.get("item", {}).get("market_cap_rank"),
                    "score": c.get("item", {}).get("score"),
                }
                for c in coins[:10]
            ]
            logger.info("Trending coins fetched: %d", len(self._trending_coins))
            return self._trending_coins
        except Exception:
            logger.warning("Failed to fetch trending coins", exc_info=True)
            return []

    async def _fetch_news(self, limit: int = 20) -> list[dict[str, Any]]:
        """CryptoCompare 최근 뉴스를 조회한다."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    _CRYPTOCOMPARE_NEWS,
                    params={"lang": "EN", "sortOrder": "latest"},
                )
                resp.raise_for_status()
                data = resp.json()

            articles = data.get("Data", [])[:limit]
            self._recent_news = [
                {
                    "title": a.get("title", ""),
                    "source": a.get("source", ""),
                    "categories": a.get("categories", ""),
                    "published_on": a.get("published_on"),
                }
                for a in articles
            ]
            logger.info("News articles fetched: %d", len(self._recent_news))
            return self._recent_news
        except Exception:
            logger.warning("Failed to fetch news", exc_info=True)
            return []

    async def _analyze_sentiment(
        self, news: list[dict[str, Any]], llm_chat: LLMChatFn
    ) -> None:
        """LLM으로 뉴스 감성을 분석한다."""
        headlines = "\n".join(
            f"- [{a['source']}] {a['title']}" for a in news[:15]
        )

        response = await llm_chat([
            Message(
                role=Role.SYSTEM,
                content=(
                    "You are a crypto market sentiment analyst. "
                    "Analyze these headlines and return a JSON object with:\n"
                    '{"overall_sentiment": float(-1 to 1), '
                    '"market_mood": "bullish"|"bearish"|"neutral", '
                    '"key_themes": [str], '
                    '"coin_sentiment": {"SYMBOL": float(-1 to 1)}}'
                ),
            ),
            Message(role=Role.USER, content=f"Recent crypto headlines:\n{headlines}"),
        ])

        try:
            import json
            result = json.loads(response.content)
            self.sentiment_scores["overall"] = result.get("overall_sentiment", 0)
            for sym, score in result.get("coin_sentiment", {}).items():
                self.sentiment_scores[sym.upper()] = score
        except (json.JSONDecodeError, AttributeError):
            logger.warning("Failed to parse sentiment LLM response")
