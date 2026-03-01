"""API 요청/응답 Pydantic 스키마.

ARCHITECTURE.md Section 8: REST API 스키마.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ============================================================
# 공통
# ============================================================

class ErrorDetail(BaseModel):
    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ============================================================
# 대시보드 (Dashboard)
# ============================================================

class PortfolioSummary(BaseModel):
    timestamp: str
    total_balance_usdt: float = 0.0
    reserve_balance_usdt: float = 0.0
    invested_balance_usdt: float = 0.0
    unrealized_pnl_usdt: float = 0.0
    unrealized_pnl_pct: float = 0.0
    realized_pnl_usdt: float = 0.0
    total_pnl_usdt: float = 0.0
    positions_count: int = 0
    active_strategies: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    risk_score: int = 0


class PositionDetail(BaseModel):
    position_id: str
    symbol: str
    side: str = "LONG"
    entry_time: str
    entry_price: float
    current_price: float | None = None
    quantity: float = 0.0
    entry_cost_usdt: float = 0.0
    current_value_usdt: float = 0.0
    unrealized_pnl_usdt: float = 0.0
    unrealized_pnl_pct: float = 0.0
    stop_loss: float | None = None
    target_price: float | None = None
    holding_type: str = "short_term"
    status: str = "active"


class PositionsResponse(BaseModel):
    positions: list[PositionDetail] = Field(default_factory=list)
    total_count: int = 0
    timestamp: str = ""


class PerformancePoint(BaseModel):
    timestamp: str
    portfolio_value: float = 0.0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    cumulative_pnl: float = 0.0
    trade_count: int = 0


class PerformanceSummary(BaseModel):
    total_return: float = 0.0
    best_day: float = 0.0
    worst_day: float = 0.0
    avg_daily_return: float = 0.0


class PerformanceResponse(BaseModel):
    period: str
    data: list[PerformancePoint] = Field(default_factory=list)
    summary: PerformanceSummary = Field(default_factory=PerformanceSummary)


# ============================================================
# 트레이딩 컨트롤 (Trading Control)
# ============================================================

class TradingToggleRequest(BaseModel):
    reason: str = ""


class TradingToggleResponse(BaseModel):
    status: str = "success"
    trading_enabled: bool
    timestamp: str


class ManualOrderRequest(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["limit", "market"] = "limit"
    price: float | None = None
    quantity: float
    reason: str = ""


class ManualOrderResponse(BaseModel):
    order_id: str
    symbol: str
    side: str
    status: str = "submitted"
    quantity: float
    price: float | None = None
    timestamp: str


class LiquidateRequest(BaseModel):
    position_id: str
    reason: str = ""


class LiquidateResponse(BaseModel):
    position_id: str
    symbol: str
    status: str = "liquidation_submitted"
    order_id: str | None = None
    quantity: float = 0.0
    timestamp: str


# ============================================================
# 설정 관리 (Configuration)
# ============================================================

class ConfigValueResponse(BaseModel):
    key: str
    value: Any
    description: str = ""


class ConfigUpdateRequest(BaseModel):
    key: str
    value: Any
    reason: str = ""
    confirm: bool = False


class ConfigUpdateResponse(BaseModel):
    status: str
    key: str
    old_value: Any
    new_value: Any
    timestamp: str


class ConfigBatchUpdateRequest(BaseModel):
    updates: list[dict[str, Any]]
    reason: str = ""


class ConfigBatchUpdateResponse(BaseModel):
    status: str
    results: list[dict[str, Any]]
    timestamp: str


class PresetInfo(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    changes: dict[str, Any] = Field(default_factory=dict)


class ApplyPresetRequest(BaseModel):
    preset_id: str
    reason: str = ""


class ApplyPresetResponse(BaseModel):
    status: str
    preset_id: str
    changes_applied: int = 0
    timestamp: str


class AuditLogEntry(BaseModel):
    id: str
    timestamp: str
    source: str = "api"
    key: str
    old_value: Any = None
    new_value: Any = None
    reason: str = ""
    status: str = "applied"


class AuditLogResponse(BaseModel):
    total: int = 0
    offset: int = 0
    limit: int = 50
    entries: list[AuditLogEntry] = Field(default_factory=list)


# ============================================================
# 에이전트 모니터링 (Agent Monitoring)
# ============================================================

class AgentStatusInfo(BaseModel):
    agent_id: str
    agent_type: str
    status: str = "unknown"
    health: str = "unknown"
    last_heartbeat: str | None = None
    uptime_seconds: float = 0.0


class AgentsStatusResponse(BaseModel):
    timestamp: str
    agents: list[AgentStatusInfo] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


# ============================================================
# 시그널 / 합의 (Signals / Consensus)
# ============================================================

class SignalInfo(BaseModel):
    signal_id: str
    timestamp: str
    symbol: str
    direction: str = ""
    strategy: str = ""
    score: int = 0
    status: str = "generated"
    consensus: dict[str, Any] | None = None


class SignalsResponse(BaseModel):
    signals: list[SignalInfo] = Field(default_factory=list)
    total: int = 0
    timestamp: str = ""


class ConsensusMetricsResponse(BaseModel):
    total_rounds: int = 0
    approved: int = 0
    rejected: int = 0
    timeout: int = 0
    veto: int = 0
    approval_rate: float = 0.0
    avg_duration_ms: float = 0.0


# ============================================================
# 시스템 상태 (System Status)
# ============================================================

class SystemStatusResponse(BaseModel):
    timestamp: str
    system: dict[str, Any] = Field(default_factory=dict)
    boot_sequence: dict[str, Any] | None = None
    resource_usage: dict[str, Any] | None = None


class AlertInfo(BaseModel):
    alert_id: str
    timestamp: str
    severity: str = "INFO"
    category: str = ""
    message: str = ""
    status: str = "active"


class AlertsResponse(BaseModel):
    alerts: list[AlertInfo] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
