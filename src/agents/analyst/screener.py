"""코인 스크리닝 모듈 - 2단계 유니버스 필터링.

Stage 1: 거래량, 블랙리스트 기반 정량 필터 (거래소 ticker 데이터 활용)
Stage 2: LLM 기반 펀더멘탈 점수 평가 (MicroAnalyzer 위임)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.exchange.client import ExchangeClient

logger = logging.getLogger(__name__)


class CoinScreener:
    """2단계 코인 스크리닝."""

    def __init__(
        self,
        screening_config: Any,
        exchange_client: ExchangeClient | None = None,
    ) -> None:
        self._cfg = screening_config
        self._exchange_client = exchange_client

    async def stage1_filter(self) -> list[dict[str, Any]]:
        """Stage 1: 거래량 + 블랙리스트 필터.

        거래소에서 전체 USDT 페어의 24h ticker를 조회하고,
        quote_volume 기준으로 필터링 → 상위 N개 선정.

        Returns:
            1차 필터를 통과한 코인 후보 목록.
        """
        cfg = self._cfg
        blacklist = set(cfg.blacklist)
        whitelist = set(cfg.whitelist)
        min_volume = cfg.min_daily_volume
        top_n = cfg.market_cap_rank

        if self._exchange_client is None:
            logger.warning("No exchange client — skipping stage1 filter")
            return []

        try:
            tickers = await self._exchange_client.fetch_tickers(
                agent_name="analyst",
            )
        except Exception:
            logger.exception("Failed to fetch tickers for screening")
            return []

        # USDT 페어만 필터 + 거래량 기준
        candidates: list[dict[str, Any]] = []
        for symbol, ticker in tickers.items():
            if not symbol.endswith("/USDT"):
                continue

            base = symbol.replace("/USDT", "")

            # 블랙리스트 제외
            if base in blacklist or symbol in blacklist:
                continue

            quote_vol = ticker.quote_volume or 0
            if quote_vol < min_volume:
                continue

            candidates.append({
                "symbol": symbol,
                "coin_id": base.lower(),
                "base": base,
                "quote": "USDT",
                "last_price": ticker.last,
                "quote_volume_24h": quote_vol,
                "change_pct_24h": (ticker.percentage or 0) / 100,
                "volume_24h": ticker.volume or 0,
            })

        # 거래량 내림차순 정렬 → 상위 N개
        candidates.sort(key=lambda x: x["quote_volume_24h"], reverse=True)
        filtered = candidates[:top_n]

        # 화이트리스트 강제 포함 (이미 없는 경우만)
        existing_symbols = {c["symbol"] for c in filtered}
        for wl_item in whitelist:
            wl_symbol = wl_item if "/" in wl_item else f"{wl_item}/USDT"
            if wl_symbol not in existing_symbols and wl_symbol in tickers:
                t = tickers[wl_symbol]
                filtered.append({
                    "symbol": wl_symbol,
                    "coin_id": wl_item.lower().replace("/usdt", ""),
                    "base": wl_symbol.split("/")[0],
                    "quote": "USDT",
                    "last_price": t.last,
                    "quote_volume_24h": t.quote_volume or 0,
                    "change_pct_24h": (t.percentage or 0) / 100,
                    "volume_24h": t.volume or 0,
                })

        logger.info(
            "Stage 1 filter: %d/%d USDT pairs (volume>=%s, top=%d)",
            len(filtered), len(tickers), min_volume, top_n,
        )
        return filtered

    def apply_min_score_filter(
        self, scored: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """최소 펀더멘탈 점수 기준 필터링 및 점수 내림차순 정렬."""
        min_score = self._cfg.min_fundamental_score
        filtered = [c for c in scored if c.get("fundamental_score", 0) >= min_score]
        filtered.sort(key=lambda x: x.get("fundamental_score", 0), reverse=True)
        return filtered
