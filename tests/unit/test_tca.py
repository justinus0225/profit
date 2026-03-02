"""TCA 모듈 단위 테스트 (P5)."""

from __future__ import annotations

import pytest

from src.core.tca import PostTradeAnalysis, PreTradeAnalysis, TCAModule


class TestPreTradeAnalysis:
    def test_basic_analysis(self, fake_redis) -> None:
        tca = TCAModule(fake_redis)
        result = tca.pre_trade_analyze(
            symbol="BTC/KRW",
            side="buy",
            quantity_usd=10_000.0,
            current_price=50_000.0,
            volume_24h=1_000_000.0,
        )
        assert isinstance(result, PreTradeAnalysis)
        assert result.symbol == "BTC/KRW"
        assert result.estimated_slippage_pct >= 0

    def test_small_order_recommends_market(self, fake_redis) -> None:
        tca = TCAModule(fake_redis)
        result = tca.pre_trade_analyze(
            symbol="BTC/KRW",
            side="buy",
            quantity_usd=1_000.0,
            current_price=50_000.0,
            volume_24h=10_000_000.0,
        )
        assert result.recommended_order_type == "market"

    def test_large_order_recommends_twap(self, fake_redis) -> None:
        tca = TCAModule(fake_redis)
        result = tca.pre_trade_analyze(
            symbol="BTC/KRW",
            side="buy",
            quantity_usd=100_000.0,
            current_price=50_000.0,
            volume_24h=500_000.0,
        )
        assert result.recommended_order_type == "twap"

    def test_spread_calculation(self, fake_redis) -> None:
        tca = TCAModule(fake_redis)
        result = tca.pre_trade_analyze(
            symbol="BTC/KRW",
            side="buy",
            quantity_usd=5_000.0,
            current_price=50_000.0,
            bid=49_950.0,
            ask=50_050.0,
        )
        # 스프레드 = (50050 - 49950) / 50000 = 0.2%
        assert result.spread_pct == pytest.approx(0.2, abs=0.01)


class TestPostTradeAnalysis:
    def test_slippage_calculation(self, fake_redis) -> None:
        tca = TCAModule(fake_redis)
        result = tca.post_trade_analyze(
            symbol="BTC/KRW",
            side="buy",
            decision_price=50_000.0,
            fill_price=50_050.0,
            quantity=1.0,
            total_usd=50_050.0,
            fee_usd=50.05,
            order_type="market",
            execution_time_ms=150.0,
        )
        assert isinstance(result, PostTradeAnalysis)
        # 슬리피지 = (50050 - 50000) / 50000 = 0.1%
        assert result.slippage_pct == pytest.approx(0.1, abs=0.01)
        assert result.implementation_shortfall_pct > 0

    def test_sell_slippage_negative(self, fake_redis) -> None:
        tca = TCAModule(fake_redis)
        result = tca.post_trade_analyze(
            symbol="BTC/KRW",
            side="sell",
            decision_price=50_000.0,
            fill_price=49_950.0,
            quantity=1.0,
            total_usd=49_950.0,
            fee_usd=49.95,
            order_type="market",
            execution_time_ms=100.0,
        )
        # 매도 슬리피지 = (50000 - 49950) / 50000 = 0.1%
        assert result.slippage_pct == pytest.approx(0.1, abs=0.01)


class TestTCASaveAndSummary:
    @pytest.mark.asyncio
    async def test_save_analysis(self, fake_redis) -> None:
        tca = TCAModule(fake_redis)
        analysis = tca.post_trade_analyze(
            symbol="BTC/KRW",
            side="buy",
            decision_price=50_000.0,
            fill_price=50_025.0,
            quantity=0.5,
            total_usd=25_012.5,
            fee_usd=25.0,
            order_type="market",
            execution_time_ms=200.0,
        )
        await tca.save_analysis(analysis)
        # Redis에 저장 확인
        stored = await fake_redis.lrange("tca:history", 0, -1)
        assert len(stored) == 1
