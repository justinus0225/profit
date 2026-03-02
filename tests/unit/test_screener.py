"""CoinScreener 단위 테스트.

Mock ExchangeClient로 코인 스크리닝 로직을 검증한다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.agents.analyst.screener import CoinScreener
from src.exchange.models import Ticker


def _make_screening_config(
    *,
    market_cap_rank: int = 50,
    min_daily_volume: int = 1_000_000,
    min_fundamental_score: int = 40,
    blacklist: list[str] | None = None,
    whitelist: list[str] | None = None,
):
    """테스트용 스크리닝 설정 mock."""

    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.market_cap_rank = market_cap_rank
    cfg.min_daily_volume = min_daily_volume
    cfg.min_fundamental_score = min_fundamental_score
    cfg.blacklist = blacklist or []
    cfg.whitelist = whitelist or []
    return cfg


def _make_ticker(
    symbol: str,
    last: float = 100.0,
    quote_volume: float = 5_000_000,
    percentage: float = 2.0,
    volume: float = 1000.0,
) -> Ticker:
    """테스트용 Ticker 생성."""
    return Ticker(
        symbol=symbol,
        timestamp=datetime.now(tz=timezone.utc),
        last=last,
        quote_volume=quote_volume,
        percentage=percentage,
        volume=volume,
    )


def _make_mock_exchange(tickers: dict[str, Ticker]) -> AsyncMock:
    """Mock ExchangeClient."""
    client = AsyncMock()
    client.fetch_tickers = AsyncMock(return_value=tickers)
    return client


class TestCoinScreener:
    """CoinScreener 테스트."""

    @pytest.mark.asyncio
    async def test_basic_volume_filter(self) -> None:
        """거래량 필터링 기본 동작 검증."""
        tickers = {
            "BTC/USDT": _make_ticker("BTC/USDT", quote_volume=50_000_000),
            "ETH/USDT": _make_ticker("ETH/USDT", quote_volume=30_000_000),
            "SHIB/USDT": _make_ticker("SHIB/USDT", quote_volume=500_000),  # 기준 미달
        }
        exchange = _make_mock_exchange(tickers)
        screener = CoinScreener(
            _make_screening_config(min_daily_volume=1_000_000),
            exchange_client=exchange,
        )

        result = await screener.stage1_filter()
        symbols = [c["symbol"] for c in result]

        assert "BTC/USDT" in symbols
        assert "ETH/USDT" in symbols
        assert "SHIB/USDT" not in symbols

    @pytest.mark.asyncio
    async def test_blacklist_exclusion(self) -> None:
        """블랙리스트 코인 제외 검증."""
        tickers = {
            "BTC/USDT": _make_ticker("BTC/USDT", quote_volume=50_000_000),
            "LUNA/USDT": _make_ticker("LUNA/USDT", quote_volume=20_000_000),
        }
        exchange = _make_mock_exchange(tickers)
        screener = CoinScreener(
            _make_screening_config(blacklist=["LUNA"]),
            exchange_client=exchange,
        )

        result = await screener.stage1_filter()
        symbols = [c["symbol"] for c in result]

        assert "BTC/USDT" in symbols
        assert "LUNA/USDT" not in symbols

    @pytest.mark.asyncio
    async def test_whitelist_inclusion(self) -> None:
        """화이트리스트 코인 강제 포함 검증."""
        tickers = {
            "BTC/USDT": _make_ticker("BTC/USDT", quote_volume=50_000_000),
            "SOL/USDT": _make_ticker("SOL/USDT", quote_volume=100_000),  # 기준 미달
        }
        exchange = _make_mock_exchange(tickers)
        screener = CoinScreener(
            _make_screening_config(min_daily_volume=1_000_000, whitelist=["SOL"]),
            exchange_client=exchange,
        )

        result = await screener.stage1_filter()
        symbols = [c["symbol"] for c in result]

        assert "BTC/USDT" in symbols
        assert "SOL/USDT" in symbols  # 거래량 미달이지만 whitelist로 포함

    @pytest.mark.asyncio
    async def test_top_n_limit(self) -> None:
        """상위 N개 제한 검증."""
        tickers = {
            f"COIN{i}/USDT": _make_ticker(
                f"COIN{i}/USDT", quote_volume=(100 - i) * 1_000_000
            )
            for i in range(10)
        }
        exchange = _make_mock_exchange(tickers)
        screener = CoinScreener(
            _make_screening_config(market_cap_rank=3),
            exchange_client=exchange,
        )

        result = await screener.stage1_filter()
        assert len(result) == 3

        # 거래량 내림차순 정렬 확인
        volumes = [c["quote_volume_24h"] for c in result]
        assert volumes == sorted(volumes, reverse=True)

    @pytest.mark.asyncio
    async def test_non_usdt_pairs_excluded(self) -> None:
        """비-USDT 페어 제외 검증."""
        tickers = {
            "BTC/USDT": _make_ticker("BTC/USDT", quote_volume=50_000_000),
            "ETH/BTC": _make_ticker("ETH/BTC", quote_volume=30_000_000),
            "SOL/EUR": _make_ticker("SOL/EUR", quote_volume=20_000_000),
        }
        exchange = _make_mock_exchange(tickers)
        screener = CoinScreener(
            _make_screening_config(),
            exchange_client=exchange,
        )

        result = await screener.stage1_filter()
        symbols = [c["symbol"] for c in result]

        assert "BTC/USDT" in symbols
        assert "ETH/BTC" not in symbols
        assert "SOL/EUR" not in symbols

    @pytest.mark.asyncio
    async def test_no_exchange_client(self) -> None:
        """ExchangeClient 없을 때 빈 리스트 반환."""
        screener = CoinScreener(_make_screening_config(), exchange_client=None)
        result = await screener.stage1_filter()
        assert result == []

    def test_apply_min_score_filter(self) -> None:
        """최소 점수 필터링 + 내림차순 정렬 검증."""
        scored = [
            {"symbol": "BTC/USDT", "fundamental_score": 80},
            {"symbol": "ETH/USDT", "fundamental_score": 30},  # 기준 미달
            {"symbol": "SOL/USDT", "fundamental_score": 60},
        ]
        screener = CoinScreener(_make_screening_config(min_fundamental_score=40))

        result = screener.apply_min_score_filter(scored)

        assert len(result) == 2
        assert result[0]["symbol"] == "BTC/USDT"
        assert result[1]["symbol"] == "SOL/USDT"

    @pytest.mark.asyncio
    async def test_result_structure(self) -> None:
        """반환 결과의 필드 구조 검증."""
        tickers = {
            "BTC/USDT": _make_ticker(
                "BTC/USDT", last=45000, quote_volume=50_000_000,
                percentage=3.5, volume=1200,
            ),
        }
        exchange = _make_mock_exchange(tickers)
        screener = CoinScreener(
            _make_screening_config(),
            exchange_client=exchange,
        )

        result = await screener.stage1_filter()
        assert len(result) == 1

        coin = result[0]
        assert coin["symbol"] == "BTC/USDT"
        assert coin["coin_id"] == "btc"
        assert coin["base"] == "BTC"
        assert coin["quote"] == "USDT"
        assert coin["last_price"] == 45000
        assert coin["quote_volume_24h"] == 50_000_000
        assert coin["change_pct_24h"] == pytest.approx(0.035)
        assert coin["volume_24h"] == 1200
