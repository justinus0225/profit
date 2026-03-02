"""Prometheus 메트릭 계측 모듈.

prometheus_client를 사용하여 시스템 메트릭을 수집한다.
/metrics 엔드포인트에서 Prometheus가 스크랩.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Info

# ── 시스템 정보 ──
SYSTEM_INFO = Info("profit_system", "P.R.O.F.I.T. system information")

# ── 에이전트 메트릭 ──
AGENT_STATUS = Gauge(
    "profit_agent_status",
    "Agent running status (1=running, 0=stopped)",
    ["agent_name"],
)
AGENT_ERRORS = Counter(
    "profit_agent_errors_total",
    "Total agent errors",
    ["agent_name", "error_type"],
)
AGENT_EVENTS_PROCESSED = Counter(
    "profit_agent_events_processed_total",
    "Total events processed by agent",
    ["agent_name", "event_type"],
)

# ── 매매 메트릭 ──
ORDERS_TOTAL = Counter(
    "profit_orders_total",
    "Total orders submitted",
    ["side", "order_type", "status"],
)
ORDER_LATENCY = Histogram(
    "profit_order_latency_seconds",
    "Order execution latency",
    ["side"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)
SLIPPAGE = Histogram(
    "profit_slippage_ratio",
    "Order slippage ratio",
    ["side"],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05],
)
POSITIONS_OPEN = Gauge(
    "profit_positions_open",
    "Number of open positions",
)
PORTFOLIO_PNL = Gauge(
    "profit_portfolio_pnl_pct",
    "Portfolio PnL percentage",
)

# ── 신호 메트릭 ──
SIGNALS_GENERATED = Counter(
    "profit_signals_generated_total",
    "Total signals generated",
    ["strategy", "direction"],
)
SIGNALS_APPROVED = Counter(
    "profit_signals_approved_total",
    "Total signals approved by consensus",
)
SIGNALS_REJECTED = Counter(
    "profit_signals_rejected_total",
    "Total signals rejected",
    ["reason"],
)

# ── 리스크 메트릭 ──
RISK_LEVEL = Gauge(
    "profit_risk_level",
    "Current risk score (0-100)",
)
CIRCUIT_BREAKER_TRIPS = Counter(
    "profit_circuit_breaker_trips_total",
    "Total circuit breaker activations",
    ["trigger"],
)
DAILY_LOSS_PCT = Gauge(
    "profit_daily_loss_pct",
    "Daily portfolio loss percentage",
)

# ── 거래소 메트릭 ──
EXCHANGE_API_CALLS = Counter(
    "profit_exchange_api_calls_total",
    "Total exchange API calls",
    ["method", "status"],
)
EXCHANGE_RATE_LIMIT_HITS = Counter(
    "profit_exchange_rate_limit_hits_total",
    "Rate limit throttle events",
)

# ── LLM 메트릭 ──
LLM_CALLS = Counter(
    "profit_llm_calls_total",
    "Total LLM API calls",
    ["provider", "status"],
)
LLM_LATENCY = Histogram(
    "profit_llm_latency_seconds",
    "LLM API call latency",
    ["provider"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)
LLM_TOKENS = Counter(
    "profit_llm_tokens_total",
    "Total LLM tokens consumed",
    ["provider", "direction"],
)

# ── 데이터 품질 ──
DATA_QUALITY_CHECKS = Counter(
    "profit_data_quality_checks_total",
    "Total data quality checks",
    ["result"],
)
DATA_ANOMALIES = Counter(
    "profit_data_anomalies_total",
    "Total data anomalies detected",
    ["symbol"],
)
