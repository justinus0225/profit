"""코인 스크리닝 모듈 - 2단계 유니버스 필터링.

Stage 1: 시총 순위, 거래량, 블랙리스트 기반 정량 필터
Stage 2: LLM 기반 펀더멘탈 점수 평가 (MicroAnalyzer 위임)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class CoinScreener:
    """2단계 코인 스크리닝."""

    def __init__(self, screening_config: Any) -> None:
        self._cfg = screening_config

    async def stage1_filter(self) -> list[dict[str, Any]]:
        """Stage 1: 시총 순위 + 거래량 + 블랙리스트 필터.

        Returns:
            1차 필터를 통과한 코인 후보 목록.

        Note:
            실제 CoinGecko/CMC API 연동은 후속 구현.
            필터 기준: market_cap_rank, min_daily_volume, blacklist, whitelist.
        """
        cfg = self._cfg
        candidates: list[dict[str, Any]] = []
        # 후속 구현: API에서 Top N 코인 조회
        # 필터: market_cap_rank <= cfg.market_cap_rank
        # 필터: daily_volume >= cfg.min_daily_volume
        # 필터: symbol not in cfg.blacklist
        # 추가: cfg.whitelist에 있으면 항상 포함
        logger.info("Stage 1 filter: market_cap<=%d, volume>=%s",
                     cfg.market_cap_rank, cfg.min_daily_volume)
        return candidates

    def apply_min_score_filter(
        self, scored: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """최소 펀더멘탈 점수 기준 필터링 및 점수 내림차순 정렬."""
        min_score = self._cfg.min_fundamental_score
        filtered = [c for c in scored if c.get("fundamental_score", 0) >= min_score]
        filtered.sort(key=lambda x: x.get("fundamental_score", 0), reverse=True)
        return filtered
