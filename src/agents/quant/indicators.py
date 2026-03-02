"""기술적 지표 계산 모듈.

RSI, MACD, Bollinger Band, ADX, ATR 등 기술적 지표를
pandas-ta 기반으로 계산한다.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
import pandas_ta as ta

from src.exchange.models import OHLCV

logger = logging.getLogger(__name__)

# 최소 캔들 수 (50-period MA에 안전 마진)
MIN_CANDLES = 60


class IndicatorEngine:
    """기술적 지표 계산 엔진."""

    def __init__(self, strategy_config: Any) -> None:
        self._strategy_cfg = strategy_config

    async def compute(
        self, ohlcv_list: list[OHLCV], symbol: str, timeframe: str
    ) -> dict[str, Any]:
        """기술적 지표를 계산한다.

        Args:
            ohlcv_list: OHLCV 캔들 목록 (시간순 정렬).
            symbol: 종목 심볼 (예: "BTC/USDT").
            timeframe: 캔들 주기 (예: "1h", "4h", "1d").

        Returns:
            RSI, MACD, BB, MA, ADX, ATR, 거래량 비율 등 지표 dict.
        """
        if len(ohlcv_list) < MIN_CANDLES:
            logger.warning(
                "Insufficient data for %s (%s): %d candles (need %d)",
                symbol, timeframe, len(ohlcv_list), MIN_CANDLES,
            )
            return self._empty_result(symbol, timeframe, len(ohlcv_list))

        df = self._to_dataframe(ohlcv_list)
        missing: list[str] = []

        rsi = self._calc_rsi(df, missing)
        macd_hist = self._calc_macd_histogram(df, missing)
        bb_pos = self._calc_bb_position(df, missing)
        ma_short, ma_long = self._calc_sma(df, missing)
        adx = self._calc_adx(df, missing)
        atr = self._calc_atr(df, missing)
        vol_ratio = self._calc_volume_ratio(df, missing)

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "rsi_14": rsi,
            "macd_histogram": macd_hist,
            "bb_position": bb_pos,
            "ma_short": ma_short,
            "ma_long": ma_long,
            "adx": adx,
            "atr": atr,
            "volume_ratio": vol_ratio,
            "data_quality": {
                "candle_count": len(ohlcv_list),
                "sufficient": len(missing) == 0,
                "missing": missing,
            },
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

    # ── 내부 유틸 ──

    @staticmethod
    def _to_dataframe(ohlcv_list: list[OHLCV]) -> pd.DataFrame:
        """OHLCV 리스트를 pandas DataFrame으로 변환한다."""
        rows = [
            {
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in ohlcv_list
        ]
        df = pd.DataFrame(rows)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df

    @staticmethod
    def _safe_last(series: pd.Series | None) -> float | None:
        """Series의 마지막 유효값을 반환한다."""
        if series is None or series.empty:
            return None
        val = series.dropna().iloc[-1] if not series.dropna().empty else None
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return None
        return float(val)

    def _calc_rsi(self, df: pd.DataFrame, missing: list[str]) -> float | None:
        """RSI(14) 계산."""
        try:
            series = ta.rsi(df["close"], length=14)
            val = self._safe_last(series)
            if val is None:
                missing.append("rsi_14")
            return val
        except Exception:
            logger.debug("RSI calculation failed", exc_info=True)
            missing.append("rsi_14")
            return None

    def _calc_macd_histogram(self, df: pd.DataFrame, missing: list[str]) -> float | None:
        """MACD(12,26,9) 히스토그램 계산."""
        try:
            macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
            if macd_df is None or macd_df.empty:
                missing.append("macd_histogram")
                return None
            hist_col = [c for c in macd_df.columns if "h" in c.lower()]
            if not hist_col:
                missing.append("macd_histogram")
                return None
            val = self._safe_last(macd_df[hist_col[0]])
            if val is None:
                missing.append("macd_histogram")
            return val
        except Exception:
            logger.debug("MACD calculation failed", exc_info=True)
            missing.append("macd_histogram")
            return None

    def _calc_bb_position(self, df: pd.DataFrame, missing: list[str]) -> float | None:
        """Bollinger Band(20,2) 포지션 (0~1) 계산.

        0 = 하단 밴드, 1 = 상단 밴드.
        """
        try:
            bb_df = ta.bbands(df["close"], length=20, std=2.0)
            if bb_df is None or bb_df.empty:
                missing.append("bb_position")
                return None
            lower_col = [c for c in bb_df.columns if "l" in c.lower() and "b" in c.lower()]
            upper_col = [c for c in bb_df.columns if "u" in c.lower() and "b" in c.lower()]
            if not lower_col or not upper_col:
                missing.append("bb_position")
                return None
            lower = bb_df[lower_col[0]].iloc[-1]
            upper = bb_df[upper_col[0]].iloc[-1]
            close = df["close"].iloc[-1]
            if np.isnan(lower) or np.isnan(upper) or upper == lower:
                missing.append("bb_position")
                return None
            pos = (close - lower) / (upper - lower)
            return float(max(0.0, min(1.0, pos)))
        except Exception:
            logger.debug("BB calculation failed", exc_info=True)
            missing.append("bb_position")
            return None

    def _calc_sma(
        self, df: pd.DataFrame, missing: list[str]
    ) -> tuple[float | None, float | None]:
        """SMA(short=20, long=50) 계산."""
        cfg = self._strategy_cfg.trend_following
        short_len = cfg.ma_short
        long_len = cfg.ma_long

        ma_short = None
        ma_long = None
        try:
            s = ta.sma(df["close"], length=short_len)
            ma_short = self._safe_last(s)
        except Exception:
            logger.debug("SMA short calculation failed", exc_info=True)
        if ma_short is None:
            missing.append("ma_short")

        try:
            s = ta.sma(df["close"], length=long_len)
            ma_long = self._safe_last(s)
        except Exception:
            logger.debug("SMA long calculation failed", exc_info=True)
        if ma_long is None:
            missing.append("ma_long")

        return ma_short, ma_long

    def _calc_adx(self, df: pd.DataFrame, missing: list[str]) -> float | None:
        """ADX(14) 계산."""
        try:
            adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
            if adx_df is None or adx_df.empty:
                missing.append("adx")
                return None
            adx_col = [c for c in adx_df.columns if c.startswith("ADX")]
            if not adx_col:
                missing.append("adx")
                return None
            val = self._safe_last(adx_df[adx_col[0]])
            if val is None:
                missing.append("adx")
            return val
        except Exception:
            logger.debug("ADX calculation failed", exc_info=True)
            missing.append("adx")
            return None

    def _calc_atr(self, df: pd.DataFrame, missing: list[str]) -> float | None:
        """ATR(14) 계산."""
        try:
            series = ta.atr(df["high"], df["low"], df["close"], length=14)
            val = self._safe_last(series)
            if val is None:
                missing.append("atr")
            return val
        except Exception:
            logger.debug("ATR calculation failed", exc_info=True)
            missing.append("atr")
            return None

    def _calc_volume_ratio(self, df: pd.DataFrame, missing: list[str]) -> float | None:
        """최근 거래량 / 20-period 평균 거래량 비율."""
        try:
            avg = df["volume"].rolling(window=20).mean()
            avg_last = avg.iloc[-1]
            vol_last = df["volume"].iloc[-1]
            if np.isnan(avg_last) or avg_last == 0:
                missing.append("volume_ratio")
                return None
            return float(vol_last / avg_last)
        except Exception:
            logger.debug("Volume ratio calculation failed", exc_info=True)
            missing.append("volume_ratio")
            return None

    @staticmethod
    def _empty_result(
        symbol: str, timeframe: str, candle_count: int
    ) -> dict[str, Any]:
        """데이터 부족 시 빈 결과를 반환한다."""
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
            "data_quality": {
                "candle_count": candle_count,
                "sufficient": False,
                "missing": ["all"],
            },
        }
