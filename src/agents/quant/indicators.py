"""기술적 지표 계산 모듈.

RSI, MACD, Bollinger Band, ADX, ATR 등 기술적 지표를
계산한다 (pandas-ta 기반, 후속 연동 예정).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class IndicatorEngine:
    """기술적 지표 계산 엔진."""

    def __init__(self, strategy_config: Any) -> None:
        self._strategy_cfg = strategy_config

    async def compute(self, symbol: str, timeframe: str) -> dict[str, Any]:
        """기술적 지표를 계산한다.

        Args:
            symbol: 종목 심볼 (예: "BTC/KRW").
            timeframe: 캔들 주기 (예: "1h", "4h", "1d").

        Returns:
            RSI, MACD, BB, MA, ADX, ATR, 거래량 비율 등 지표 dict.

        Note:
            pandas-ta 기반 실제 계산은 후속 구현 예정.
        """
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "rsi_14": None,
            "macd_histogram": None,
            "bb_position": None,
            "ma_short": None,
            "ma_long": None,
            "adx": None,
            "atr": None,
            "volume_ratio": None,
        }

    def exceeds_threshold(self, indicators: dict[str, Any]) -> bool:
        """빠른 스캔에서 임계값 초과 여부를 판단한다.

        RSI가 과매수/과매도 영역에 진입하면 True.
        """
        rsi = indicators.get("rsi_14")
        if rsi is None:
            return False
        cfg = self._strategy_cfg.mean_reversion
        return rsi <= cfg.rsi_oversold or rsi >= cfg.rsi_overbought
