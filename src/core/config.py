"""P.R.O.F.I.T. 설정 관리 시스템.

config/default.yml의 146개 설정값을 Pydantic v2 모델로 1:1 매핑.
YAML 로딩 + 유효성 검증 + 환경 변수 오버라이드를 지원한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


# ============================================================
# 자금 관리 (Fund Management)
# ============================================================

class DCAConfig(BaseModel):
    phases: int = Field(default=3, ge=1, le=5)
    phase1_ratio: float = Field(default=0.40, ge=0.30, le=1.00)
    phase2_trigger: float = Field(default=-0.02, ge=-0.10, le=-0.01)
    phase3_trigger: float = Field(default=-0.05, ge=-0.15, le=-0.02)


class FundConfig(BaseModel):
    reserve_ratio: float = Field(default=0.30, ge=0.10, le=0.50)
    max_single_position: float = Field(default=0.20, ge=0.05, le=0.40)
    max_concurrent_coins: int = Field(default=10, ge=3, le=20)
    dca: DCAConfig = Field(default_factory=DCAConfig)


# ============================================================
# 리스크 관리 (Risk Management)
# ============================================================

class CircuitBreakerConfig(BaseModel):
    price_spike: float = Field(default=0.10, ge=0.05, le=0.20)
    api_failures: int = Field(default=3, ge=2, le=10)


class RiskLevelsConfig(BaseModel):
    low_max: int = Field(default=30, ge=20, le=40)
    medium_max: int = Field(default=60, ge=40, le=70)
    high_max: int = Field(default=80, ge=60, le=90)


class RiskUtilizationConfig(BaseModel):
    low: float = Field(default=1.00, ge=0.80, le=1.00)
    medium: float = Field(default=0.70, ge=0.50, le=0.90)
    high: float = Field(default=0.40, ge=0.20, le=0.60)


class RiskConfig(BaseModel):
    daily_loss_limit: float = Field(default=-0.03, ge=-0.10, le=-0.01)
    total_loss_limit: float = Field(default=-0.10, ge=-0.30, le=-0.05)
    default_stop_loss: float = Field(default=-0.05, ge=-0.15, le=-0.02)
    trailing_stop: float = Field(default=0.03, ge=0.01, le=0.10)
    max_consecutive_losses: int = Field(default=5, ge=3, le=10)
    slippage_tolerance: float = Field(default=0.005, ge=0.001, le=0.02)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    levels: RiskLevelsConfig = Field(default_factory=RiskLevelsConfig)
    utilization: RiskUtilizationConfig = Field(default_factory=RiskUtilizationConfig)


# ============================================================
# 코인 선별 (Universe Screening)
# ============================================================

class UnlockWarningConfig(BaseModel):
    days: int = Field(default=30, ge=7, le=90)
    ratio: float = Field(default=0.05, ge=0.02, le=0.15)


class ScreeningConfig(BaseModel):
    market_cap_rank: int = Field(default=100, ge=20, le=500)
    min_daily_volume: int = Field(default=10_000_000, ge=1_000_000, le=100_000_000)
    min_fundamental_score: int = Field(default=40, ge=20, le=60)
    unlock_warning: UnlockWarningConfig = Field(default_factory=UnlockWarningConfig)
    blacklist: list[str] = Field(default_factory=list)
    whitelist: list[str] = Field(default_factory=list)
    exchange: str = Field(default="binance")


# ============================================================
# 시그널 및 합의 (Signal & Consensus)
# ============================================================

class SignalConfig(BaseModel):
    buy_threshold: int = Field(default=50, ge=30, le=80)
    sell_threshold: int = Field(default=-50, ge=-80, le=-30)
    consensus_similarity_min: float = Field(default=0.60, ge=0.40, le=0.80)
    consensus_quorum: int = Field(default=2, ge=2, le=3)


# ============================================================
# 전략 (Strategies)
# ============================================================

class StrategyWeightConfig(BaseModel):
    """전략별 가중치 공통."""


class MeanReversionWeightConfig(BaseModel):
    rsi: float = Field(default=0.40)
    bollinger: float = Field(default=0.30)
    macd: float = Field(default=0.30)


class MeanReversionConfig(BaseModel):
    enabled: bool = True
    rsi_oversold: int = Field(default=30, ge=15, le=40)
    rsi_overbought: int = Field(default=70, ge=60, le=85)
    weight: MeanReversionWeightConfig = Field(default_factory=MeanReversionWeightConfig)


class TrendFollowingWeightConfig(BaseModel):
    ma: float = Field(default=0.35)
    adx: float = Field(default=0.35)
    volume: float = Field(default=0.30)


class TrendFollowingConfig(BaseModel):
    enabled: bool = True
    ma_short: int = Field(default=20, ge=5, le=50)
    ma_long: int = Field(default=50, ge=20, le=200)
    adx_min: int = Field(default=25, ge=15, le=40)
    weight: TrendFollowingWeightConfig = Field(default_factory=TrendFollowingWeightConfig)


class MomentumWeightConfig(BaseModel):
    price: float = Field(default=0.40)
    volume: float = Field(default=0.40)
    rsi: float = Field(default=0.20)


class MomentumConfig(BaseModel):
    enabled: bool = True
    price_spike_threshold: float = Field(default=0.03, ge=0.02, le=0.10)
    volume_spike_multiplier: int = Field(default=5, ge=3, le=10)
    weight: MomentumWeightConfig = Field(default_factory=MomentumWeightConfig)


class BreakoutWeightConfig(BaseModel):
    breakout_strength: float = Field(default=0.40)
    atr: float = Field(default=0.30)
    volume: float = Field(default=0.30)


class BreakoutConfig(BaseModel):
    enabled: bool = True
    lookback_days: int = Field(default=20, ge=10, le=60)
    weight: BreakoutWeightConfig = Field(default_factory=BreakoutWeightConfig)


class StrategyConfig(BaseModel):
    mean_reversion: MeanReversionConfig = Field(default_factory=MeanReversionConfig)
    trend_following: TrendFollowingConfig = Field(default_factory=TrendFollowingConfig)
    momentum: MomentumConfig = Field(default_factory=MomentumConfig)
    breakout: BreakoutConfig = Field(default_factory=BreakoutConfig)


# ============================================================
# 포트폴리오 관리 (Portfolio Management)
# ============================================================

class AllocationConfig(BaseModel):
    short_term: float = Field(default=0.25, ge=0.10, le=0.40)
    mid_term: float = Field(default=0.45, ge=0.30, le=0.60)
    long_term: float = Field(default=0.30, ge=0.10, le=0.40)


class MaxHoldingDaysConfig(BaseModel):
    short_term: int = Field(default=7, ge=1, le=14)
    mid_term: int = Field(default=28, ge=7, le=60)


class ExtendConditionsConfig(BaseModel):
    min_pnl: float = Field(default=0.00, ge=-0.02, le=0.05)
    min_fundamental_score: int = Field(default=70, ge=40, le=90)
    max_risk_level: int = Field(default=60, ge=30, le=80)


class PortfolioConfig(BaseModel):
    allocation: AllocationConfig = Field(default_factory=AllocationConfig)
    max_correlation: float = Field(default=0.80, ge=0.50, le=0.95)
    max_holding_days: MaxHoldingDaysConfig = Field(default_factory=MaxHoldingDaysConfig)
    rebalance_time: str = Field(default="00:00")
    extend_conditions: ExtendConditionsConfig = Field(default_factory=ExtendConditionsConfig)


# ============================================================
# 스캔 주기 (Scan Schedule)
# ============================================================

class QuantScheduleConfig(BaseModel):
    fast_scan_minutes: int = Field(default=15, ge=5, le=60)
    deep_scan_minutes: int = Field(default=60, ge=30, le=240)
    strategy_eval_minutes: int = Field(default=240, ge=60, le=480)


class AnalystScheduleConfig(BaseModel):
    news_crawl_minutes: int = Field(default=60, ge=15, le=240)
    macro_update_minutes: int = Field(default=240, ge=60, le=480)
    universe_update_time: str = Field(default="00:00")


class RiskScheduleConfig(BaseModel):
    position_poll_seconds: int = Field(default=10, ge=5, le=60)
    full_eval_time: str = Field(default="00:00")


class PortfolioScheduleConfig(BaseModel):
    report_time: str = Field(default="09:00")


class ExecutionScheduleConfig(BaseModel):
    order_poll_seconds: int = Field(default=30, ge=10, le=120)


class OMSScheduleConfig(BaseModel):
    reconciliation_seconds: int = Field(default=300, ge=60, le=600)


class ScheduleConfig(BaseModel):
    quant: QuantScheduleConfig = Field(default_factory=QuantScheduleConfig)
    analyst: AnalystScheduleConfig = Field(default_factory=AnalystScheduleConfig)
    risk: RiskScheduleConfig = Field(default_factory=RiskScheduleConfig)
    portfolio: PortfolioScheduleConfig = Field(default_factory=PortfolioScheduleConfig)
    execution: ExecutionScheduleConfig = Field(default_factory=ExecutionScheduleConfig)
    oms: OMSScheduleConfig = Field(default_factory=OMSScheduleConfig)


# ============================================================
# 이벤트 트리거 (Event Triggers)
# ============================================================

class PriceSpikeConfig(BaseModel):
    window_minutes: int = Field(default=5, ge=1, le=15)
    threshold: float = Field(default=0.03, ge=0.02, le=0.10)


class FearGreedConfig(BaseModel):
    extreme_fear: int = Field(default=20, ge=10, le=30)
    extreme_greed: int = Field(default=80, ge=70, le=90)


class EventConfig(BaseModel):
    price_spike: PriceSpikeConfig = Field(default_factory=PriceSpikeConfig)
    volume_spike_multiplier: int = Field(default=5, ge=3, le=20)
    fear_greed: FearGreedConfig = Field(default_factory=FearGreedConfig)


# ============================================================
# 매매 실행 (Execution)
# ============================================================

class TWAPConfig(BaseModel):
    intervals: int = Field(default=5, ge=3, le=20)
    interval_seconds: int = Field(default=60, ge=30, le=300)


class ExecutionConfig(BaseModel):
    default_order_type: str = Field(default="limit")
    limit_order_timeout: int = Field(default=300, ge=60, le=3600)
    large_order_threshold: int = Field(default=50000, ge=10000, le=500000)
    twap: TWAPConfig = Field(default_factory=TWAPConfig)


# ============================================================
# 거래소 API Rate Limiting
# ============================================================

class AgentPriorityConfig(BaseModel):
    executor: int = Field(default=10, ge=1, le=10)
    oms: int = Field(default=5, ge=1, le=10)
    quant: int = Field(default=3, ge=1, le=10)
    data_engineer: int = Field(default=3, ge=1, le=10)


class RateLimitConfig(BaseModel):
    enabled: bool = True
    max_weight_per_minute: int = Field(default=1000, ge=500, le=1200)
    max_orders_per_second: int = Field(default=8, ge=1, le=10)
    agent_priority: AgentPriorityConfig = Field(default_factory=AgentPriorityConfig)
    backoff_max_retries: int = Field(default=5, ge=1, le=10)
    backpressure_wait_max_seconds: int = Field(default=30, ge=5, le=120)


class ExchangeConfig(BaseModel):
    exchange_id: str = Field(default="binance")
    paper_trading: bool = Field(default=False)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)


# ============================================================
# 동시성 제어 (Concurrency Control)
# ============================================================

class ConcurrencyConfig(BaseModel):
    lock_backend: str = Field(default="redis")
    order_lock_ttl_seconds: int = Field(default=5, ge=2, le=30)
    balance_lock_ttl_seconds: int = Field(default=10, ge=5, le=30)
    lock_retry_attempts: int = Field(default=3, ge=1, le=10)
    lock_retry_delay_ms: int = Field(default=100, ge=50, le=1000)


# ============================================================
# 알림 (Notifications)
# ============================================================

class NotificationEventsConfig(BaseModel):
    trade_executed: bool = True
    daily_report: bool = True
    risk_level_change: bool = True
    circuit_breaker: bool = True
    stop_loss_triggered: bool = True
    config_changed: bool = True
    system_error: bool = True


class NotificationConfig(BaseModel):
    channel: str = Field(default="openclaw")
    min_level: str = Field(default="warning")
    events: NotificationEventsConfig = Field(default_factory=NotificationEventsConfig)


# ============================================================
# LLM 프로바이더 (LLM Provider)
# ============================================================

class LLMRetryConfig(BaseModel):
    max_retries: int = Field(default=3, ge=1, le=10)
    backoff: str = Field(default="exponential")
    initial_delay_seconds: float = Field(default=1.0, ge=0.5, le=5.0)


class LLMFallbackConfig(BaseModel):
    consecutive_failures: int = Field(default=5, ge=3, le=20)
    recovery_check_minutes: int = Field(default=5, ge=1, le=30)
    auto_recover: bool = True


class AgentOverrideConfig(BaseModel):
    provider: str | None = None
    model: str | None = None


class LLMCostConfig(BaseModel):
    daily_limit_usd: float = Field(default=50.0, ge=5.0, le=500.0)
    alert_threshold: float = Field(default=0.80, ge=0.50, le=0.95)


class LLMConfig(BaseModel):
    default_provider: str = Field(default="claude")
    default_model: str = Field(default="claude-sonnet-4-6")
    fallback_provider: str = Field(default="gemini")
    fallback_model: str = Field(default="gemini-2.5-pro")
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, ge=256, le=32768)
    retry: LLMRetryConfig = Field(default_factory=LLMRetryConfig)
    fallback: LLMFallbackConfig = Field(default_factory=LLMFallbackConfig)
    agent_overrides: dict[str, AgentOverrideConfig] = Field(default_factory=lambda: {
        "orchestrator": AgentOverrideConfig(model="claude-opus-4-6"),
        "analyst_sentiment": AgentOverrideConfig(model="claude-haiku-4-5"),
        "executor": AgentOverrideConfig(model="claude-haiku-4-5"),
    })
    cost: LLMCostConfig = Field(default_factory=LLMCostConfig)


# ============================================================
# 시스템 전체 제어 (System Control)
# ============================================================

class SystemConfig(BaseModel):
    trading_enabled: bool = True
    paper_trading_mode: bool = False
    maintenance_mode: bool = False


# ============================================================
# 데이터 품질 (Data Quality)
# ============================================================

class DataQualityConfig(BaseModel):
    zscore_threshold: float = Field(default=4.0, ge=2.0, le=10.0)
    iqr_multiplier: float = Field(default=3.0, ge=1.5, le=5.0)
    window_size: int = Field(default=100, ge=20, le=500)
    healing_method: str = Field(default="linear_interpolation")
    anomaly_halt_ratio: float = Field(default=0.30, ge=0.10, le=0.80)
    anomaly_halt_window_minutes: int = Field(default=10, ge=5, le=60)
    quarantine_enabled: bool = True


# ============================================================
# LLM 메모리 (LLM Memory)
# ============================================================

class AgentMaxInputTokensConfig(BaseModel):
    orchestrator: int = Field(default=16384)
    analyst_macro: int = Field(default=8192)
    analyst_micro: int = Field(default=8192)
    quant: int = Field(default=8192)
    risk: int = Field(default=4096)
    portfolio: int = Field(default=4096)
    executor: int = Field(default=2048)
    analyst_sentiment: int = Field(default=2048)


class LLMMemoryConfig(BaseModel):
    short_term_ttl_hours: int = Field(default=24, ge=1, le=168)
    short_term_max_entries: int = Field(default=50, ge=10, le=200)
    rag_enabled: bool = True
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_similarity_threshold: float = Field(default=0.70, ge=0.50, le=0.95)
    compression_enabled: bool = True
    compression_model: str = Field(default="claude-haiku-4-5")
    embedding_dimension: int = Field(default=768, ge=256, le=1536)
    agent_max_input_tokens: AgentMaxInputTokensConfig = Field(
        default_factory=AgentMaxInputTokensConfig
    )


# ============================================================
# 부트 시퀀스 (Boot Sequence)
# ============================================================

class BootConfig(BaseModel):
    candle_backfill_multiplier: float = Field(default=1.5, ge=1.0, le=5.0)
    warmup_timeout_minutes: int = Field(default=30, ge=10, le=120)
    partial_activation: bool = True
    auto_enable_trading: bool = True
    infra_retry_attempts: int = Field(default=3, ge=1, le=10)
    infra_retry_delay_seconds: int = Field(default=5, ge=1, le=30)


# ============================================================
# DB 커넥션 풀링 (DB Connection Pooling)
# ============================================================

class AgentPoolConfig(BaseModel):
    pool_size: int = Field(default=2, ge=1, le=10)
    max_overflow: int = Field(default=3, ge=0, le=20)


class DBPoolConfig(BaseModel):
    pgbouncer_host: str = Field(default="pgbouncer")
    pgbouncer_port: int = Field(default=6432, ge=1024, le=65535)
    pool_mode: str = Field(default="transaction")
    default_pool_size: int = Field(default=20, ge=5, le=100)
    max_client_conn: int = Field(default=200, ge=50, le=500)
    reserve_pool_size: int = Field(default=5, ge=0, le=20)
    sqlalchemy_pool_size: int = Field(default=2, ge=1, le=10)
    sqlalchemy_max_overflow: int = Field(default=3, ge=0, le=20)
    sqlalchemy_pool_timeout: int = Field(default=30, ge=5, le=120)
    sqlalchemy_pool_recycle: int = Field(default=3600, ge=300, le=7200)
    postgres_max_connections: int = Field(default=50, ge=20, le=200)
    health_check_interval: int = Field(default=30, ge=10, le=300)
    agent_pools: dict[str, AgentPoolConfig] = Field(default_factory=lambda: {
        "data_engineer": AgentPoolConfig(pool_size=3, max_overflow=5),
        "quant": AgentPoolConfig(pool_size=2, max_overflow=3),
        "executor": AgentPoolConfig(pool_size=2, max_overflow=3),
        "fastapi": AgentPoolConfig(pool_size=3, max_overflow=5),
    })


class DBConfig(BaseModel):
    pool: DBPoolConfig = Field(default_factory=DBPoolConfig)


# ============================================================
# 프리셋 (Presets)
# ============================================================

class PresetValues(BaseModel):
    """프리셋에 포함되는 설정값 (dot notation key → value)."""
    values: dict[str, Any] = Field(default_factory=dict)
    use_defaults: bool = Field(default=False, alias="_use_defaults")

    model_config = {"populate_by_name": True}


# ============================================================
# 루트 설정 모델
# ============================================================

class ProfitConfig(BaseModel):
    """P.R.O.F.I.T. 시스템 전체 설정 (config/default.yml 1:1 매핑)."""

    fund: FundConfig = Field(default_factory=FundConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    screening: ScreeningConfig = Field(default_factory=ScreeningConfig)
    signal: SignalConfig = Field(default_factory=SignalConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    event: EventConfig = Field(default_factory=EventConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    data_quality: DataQualityConfig = Field(default_factory=DataQualityConfig)
    llm_memory: LLMMemoryConfig = Field(default_factory=LLMMemoryConfig)
    boot: BootConfig = Field(default_factory=BootConfig)
    db: DBConfig = Field(default_factory=DBConfig)
    presets: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ============================================================
# 설정 로더
# ============================================================

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "default.yml"


class ConfigManager:
    """설정 관리 싱글톤.

    YAML 파일에서 설정을 로딩하고, Pydantic 모델로 유효성 검증한다.
    """

    _instance: ConfigManager | None = None
    _config: ProfitConfig | None = None

    def __new__(cls) -> ConfigManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_path: Path | None = None) -> None:
        if self._config is not None:
            return
        path = config_path or _DEFAULT_CONFIG_PATH
        self._config = self._load(path)

    @staticmethod
    def _load(path: Path) -> ProfitConfig:
        if not path.exists():
            return ProfitConfig()
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        return ProfitConfig.model_validate(raw)

    @property
    def config(self) -> ProfitConfig:
        if self._config is None:
            raise RuntimeError("ConfigManager not initialized")
        return self._config

    def reload(self, path: Path | None = None) -> ProfitConfig:
        """설정 파일을 다시 로딩한다."""
        p = path or _DEFAULT_CONFIG_PATH
        self._config = self._load(p)
        return self._config

    @classmethod
    def reset(cls) -> None:
        """싱글톤 인스턴스를 초기화한다 (테스트용)."""
        cls._instance = None
        cls._config = None


def get_config() -> ProfitConfig:
    """현재 설정을 반환하는 편의 함수."""
    return ConfigManager().config
