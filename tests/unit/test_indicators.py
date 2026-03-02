"""IndicatorEngine 단위 테스트.

합성 OHLCV 데이터로 기술적 지표 계산을 검증한다.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone

import pytest

from src.agents.quant.indicators import IndicatorEngine
from src.exchange.models import OHLCV


def _make_strategy_config():
    """테스트용 전략 설정 mock."""

    class _MeanReversion:
        rsi_oversold = 30
        rsi_overbought = 70

        class weight:
            rsi = 0.40
            bb = 0.40
            volume = 0.20

    class _TrendFollowing:
        ma_short = 20
        ma_long = 50
        adx_min = 25

        class weight:
            ma = 0.35
            adx = 0.35
            volume = 0.30

    class _Cfg:
        mean_reversion = _MeanReversion()
        trend_following = _TrendFollowing()

    return _Cfg()


def _generate_ohlcv(count: int = 200, base_price: float = 50000.0) -> list[OHLCV]:
    """합성 OHLCV 캔들 데이터를 생성한다.

    사인 곡선 기반으로 자연스러운 가격 변동을 시뮬레이션.
    """
    candles = []
    now = datetime.now(tz=timezone.utc)
    for i in range(count):
        # 사인 파 기반 가격 변동 (±5%)
        wave = math.sin(i * 0.1) * 0.05
        close = base_price * (1 + wave)
        high = close * 1.01
        low = close * 0.99
        open_price = close * (1 + math.sin(i * 0.05) * 0.005)
        volume = 100 + abs(math.sin(i * 0.2)) * 200

        candles.append(
            OHLCV(
                timestamp=now - timedelta(hours=count - i),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
    return candles


class TestIndicatorEngine:
    """IndicatorEngine 테스트."""

    def setup_method(self) -> None:
        self.engine = IndicatorEngine(_make_strategy_config())

    @pytest.mark.asyncio
    async def test_compute_with_sufficient_data(self) -> None:
        """200개 캔들로 모든 지표가 계산되는지 검증."""
        ohlcv = _generate_ohlcv(200)
        result = await self.engine.compute(ohlcv, "BTC/USDT", "1h")

        assert result["symbol"] == "BTC/USDT"
        assert result["timeframe"] == "1h"

        # 모든 지표가 non-None
        for key in ("rsi_14", "macd_histogram", "bb_position",
                     "ma_short", "ma_long", "adx", "atr", "volume_ratio"):
            assert result[key] is not None, f"{key} should not be None"

        # data_quality
        assert result["data_quality"]["candle_count"] == 200
        assert result["data_quality"]["sufficient"] is True
        assert result["data_quality"]["missing"] == []

    @pytest.mark.asyncio
    async def test_compute_with_insufficient_data(self) -> None:
        """데이터 부족 시 빈 결과를 반환하는지 검증."""
        ohlcv = _generate_ohlcv(10)
        result = await self.engine.compute(ohlcv, "ETH/USDT", "4h")

        assert result["symbol"] == "ETH/USDT"
        assert result["rsi_14"] is None
        assert result["data_quality"]["sufficient"] is False

    @pytest.mark.asyncio
    async def test_rsi_range(self) -> None:
        """RSI 값이 0~100 범위인지 검증."""
        ohlcv = _generate_ohlcv(200)
        result = await self.engine.compute(ohlcv, "BTC/USDT", "1h")

        rsi = result["rsi_14"]
        assert rsi is not None
        assert 0 <= rsi <= 100

    @pytest.mark.asyncio
    async def test_bb_position_range(self) -> None:
        """BB 포지션이 0~1 범위인지 검증."""
        ohlcv = _generate_ohlcv(200)
        result = await self.engine.compute(ohlcv, "BTC/USDT", "1h")

        bb_pos = result["bb_position"]
        assert bb_pos is not None
        assert 0 <= bb_pos <= 1

    @pytest.mark.asyncio
    async def test_volume_ratio_positive(self) -> None:
        """거래량 비율이 양수인지 검증."""
        ohlcv = _generate_ohlcv(200)
        result = await self.engine.compute(ohlcv, "BTC/USDT", "1h")

        vol_ratio = result["volume_ratio"]
        assert vol_ratio is not None
        assert vol_ratio > 0

    def test_exceeds_threshold_oversold(self) -> None:
        """RSI 과매도 시 True 반환 검증."""
        indicators = {"rsi_14": 25}
        assert self.engine.exceeds_threshold(indicators) is True

    def test_exceeds_threshold_overbought(self) -> None:
        """RSI 과매수 시 True 반환 검증."""
        indicators = {"rsi_14": 75}
        assert self.engine.exceeds_threshold(indicators) is True

    def test_exceeds_threshold_normal(self) -> None:
        """RSI 정상 범위 시 False 반환 검증."""
        indicators = {"rsi_14": 50}
        assert self.engine.exceeds_threshold(indicators) is False

    def test_exceeds_threshold_none(self) -> None:
        """RSI가 None이면 False 반환 검증."""
        indicators = {"rsi_14": None}
        assert self.engine.exceeds_threshold(indicators) is False

    @pytest.mark.asyncio
    async def test_empty_list(self) -> None:
        """빈 OHLCV 리스트 시 안전하게 빈 결과 반환."""
        result = await self.engine.compute([], "XRP/USDT", "1d")
        assert result["rsi_14"] is None
        assert result["data_quality"]["candle_count"] == 0
