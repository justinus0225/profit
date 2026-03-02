"""설정 관리 시스템 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.config import (
    ConfigManager,
    DCAConfig,
    FundConfig,
    ProfitConfig,
    RiskConfig,
)


class TestProfitConfig:
    """ProfitConfig 기본값 및 유효성 검증 테스트."""

    def test_default_fund_config(self) -> None:
        config = ProfitConfig()
        assert config.fund.reserve_ratio == 0.30
        assert config.fund.max_single_position == 0.20
        assert config.fund.max_concurrent_coins == 10

    def test_default_dca_config(self) -> None:
        config = ProfitConfig()
        assert config.fund.dca.phases == 3
        assert config.fund.dca.phase1_ratio == 0.40

    def test_default_risk_config(self) -> None:
        config = ProfitConfig()
        assert config.risk.stop_loss == -0.07
        assert config.risk.take_profit == 0.15
        assert config.risk.max_daily_loss == -0.05

    def test_default_concurrency_config(self) -> None:
        config = ProfitConfig()
        assert config.concurrency.order_lock_ttl_seconds == 5
        assert config.concurrency.balance_lock_ttl_seconds == 10
        assert config.concurrency.lock_retry_attempts == 3

    def test_default_llm_memory_config(self) -> None:
        config = ProfitConfig()
        assert config.llm_memory.short_term_ttl_hours == 24
        assert config.llm_memory.rag_enabled is True
        assert config.llm_memory.rag_top_k == 5
        assert config.llm_memory.rag_similarity_threshold == 0.70

    def test_fund_reserve_ratio_validation(self) -> None:
        # 유효 범위: 0.10 ~ 0.50
        with pytest.raises(Exception):
            FundConfig(reserve_ratio=0.05)
        with pytest.raises(Exception):
            FundConfig(reserve_ratio=0.60)

    def test_dca_phases_validation(self) -> None:
        # 유효 범위: 1 ~ 5
        with pytest.raises(Exception):
            DCAConfig(phases=0)
        with pytest.raises(Exception):
            DCAConfig(phases=6)

    def test_system_config_defaults(self) -> None:
        config = ProfitConfig()
        assert config.system.paper_trading_mode is True
        assert config.system.trading_enabled is False

    def test_exchange_config_defaults(self) -> None:
        config = ProfitConfig()
        assert config.exchange.exchange_id == "binance"
        assert config.exchange.paper_trading is False


class TestConfigManager:
    """ConfigManager 테스트."""

    def setup_method(self) -> None:
        ConfigManager.reset()

    def test_default_config_creation(self) -> None:
        cm = ConfigManager()
        assert isinstance(cm.config, ProfitConfig)

    def test_config_singleton(self) -> None:
        cm1 = ConfigManager()
        cm2 = ConfigManager()
        assert cm1.config is cm2.config

    def test_config_reset(self) -> None:
        cm1 = ConfigManager()
        config1 = cm1.config
        ConfigManager.reset()
        cm2 = ConfigManager()
        assert config1 is not cm2.config

    def test_yaml_loading(self) -> None:
        config_path = Path("config/default.yml")
        if config_path.exists():
            cm = ConfigManager(config_path=config_path)
            assert cm.config.fund.reserve_ratio == 0.30

    def test_reload(self) -> None:
        cm = ConfigManager()
        new_config = cm.reload()
        assert isinstance(new_config, ProfitConfig)
