"""Initial schema: 전체 테이블 + TimescaleDB hypertable + pgvector 확장.

Revision ID: 001
Revises: None
Create Date: 2026-03-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 확장 설치 ──
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")

    # ── 1. coins (마스터 테이블) ──
    op.create_table(
        "coins",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("symbol", sa.String(10), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("coingecko_id", sa.String(50), unique=True),
        sa.Column("market_cap_rank", sa.Integer),
        sa.Column("market_cap_usd", sa.Float),
        sa.Column("current_price_usd", sa.Float),
        sa.Column("volume_24h_usd", sa.Float),
        sa.Column("fundamental_score", sa.Integer, server_default="0"),
        sa.Column("last_fundamental_update", sa.DateTime(timezone=True)),
        sa.Column("token_unlock_warning", sa.Boolean, server_default="false"),
        sa.Column("unlock_ratio", sa.Float),
        sa.Column("unlock_days_remaining", sa.Integer),
        sa.Column("is_blacklisted", sa.Boolean, server_default="false"),
        sa.Column("is_whitelisted", sa.Boolean, server_default="false"),
        sa.Column("trading_enabled", sa.Boolean, server_default="true"),
        sa.Column("available_exchanges", ARRAY(sa.String)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── 2. market_data (TimescaleDB hypertable) ──
    op.create_table(
        "market_data",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coin_id", UUID(as_uuid=True), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("open", sa.Float, nullable=False),
        sa.Column("high", sa.Float, nullable=False),
        sa.Column("low", sa.Float, nullable=False),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("volume", sa.Float, nullable=False),
        sa.Column("volume_usd", sa.Float),
        sa.Column("healing_applied", sa.Boolean, server_default="false"),
        sa.Column("healing_method", sa.String(50)),
        sa.Column("quarantine_reason", sa.Text),
        sa.Column("quote_asset", sa.String(20), server_default="USDT"),
        sa.Column("exchange_name", sa.String(50), server_default="binance"),
        sa.Column("source_type", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("time", "coin_id", "timeframe"),
    )
    op.execute("SELECT create_hypertable('market_data', 'time', if_not_exists => TRUE)")
    op.create_index("idx_market_data_coin_tf", "market_data", ["coin_id", "timeframe", sa.text("time DESC")])

    # ── 3. orders (OMS 상태 머신 P1) ──
    op.create_table(
        "orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("idempotency_key", UUID(as_uuid=True), unique=True, nullable=False),
        sa.Column("exchange_order_id", sa.String(100)),
        sa.Column("coin_id", UUID(as_uuid=True), sa.ForeignKey("coins.id"), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="CREATED"),
        sa.Column("order_type", sa.String(20), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("price", sa.Float),
        sa.Column("quantity_filled", sa.Float, server_default="0"),
        sa.Column("quantity_remaining", sa.Float),
        sa.Column("average_fill_price", sa.Float),
        sa.Column("execution_agent_id", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("filled_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("last_status_update", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("error_message", sa.Text),
        sa.Column("last_reconciliation_at", sa.DateTime(timezone=True)),
        sa.Column("reconciliation_status", sa.String(50)),
        sa.CheckConstraint("quantity > 0", name="orders_quantity_positive"),
        sa.CheckConstraint("price > 0 OR price IS NULL", name="orders_price_positive"),
    )
    op.create_index("idx_orders_state", "orders", ["state"])
    op.create_index("idx_orders_coin_id", "orders", ["coin_id"])

    # ── 4. positions ──
    op.create_table(
        "positions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("coin_id", UUID(as_uuid=True), sa.ForeignKey("coins.id"), nullable=False),
        sa.Column("entry_order_id", UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.Float, nullable=False),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("holding_type", sa.String(20), nullable=False),
        sa.Column("max_holding_days", sa.Integer),
        sa.Column("target_close_date", sa.Date),
        sa.Column("target_price", sa.Float),
        sa.Column("stop_loss_price", sa.Float),
        sa.Column("trailing_stop_pct", sa.Float),
        sa.Column("current_quantity", sa.Float, nullable=False),
        sa.Column("current_price_usd", sa.Float),
        sa.Column("unrealized_pnl_usd", sa.Float),
        sa.Column("unrealized_pnl_pct", sa.Float),
        sa.Column("entry_strategy", sa.String(100)),
        sa.Column("entry_signal_score", sa.Float),
        sa.Column("entry_fees_usd", sa.Float, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("is_stop_loss_active", sa.Boolean, server_default="false"),
        sa.Column("is_trailing_stop_active", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_updated", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_positions_status", "positions", ["status"])
    op.create_index("idx_positions_coin_id", "positions", ["coin_id"])

    # ── 5. trades (TimescaleDB hypertable) ──
    op.create_table(
        "trades",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coin_id", UUID(as_uuid=True), sa.ForeignKey("coins.id"), nullable=False),
        sa.Column("position_id", UUID(as_uuid=True), sa.ForeignKey("positions.id")),
        sa.Column("order_id", UUID(as_uuid=True), sa.ForeignKey("orders.id")),
        sa.Column("trade_type", sa.String(20), nullable=False),
        sa.Column("order_side", sa.String(10), nullable=False),
        sa.Column("order_type", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("total_usd", sa.Float, nullable=False),
        sa.Column("expected_price", sa.Float),
        sa.Column("slippage_pct", sa.Float),
        sa.Column("fee_usd", sa.Float, server_default="0"),
        sa.Column("execution_agent_id", sa.String(50)),
        sa.Column("strategy_name", sa.String(100)),
        sa.Column("signal_score", sa.Float),
        sa.Column("exchange_name", sa.String(50), server_default="binance"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_trades_coin_time", "trades", ["coin_id", sa.text("time DESC")])

    # ── 6. signals (TimescaleDB hypertable) ──
    op.create_table(
        "signals",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coin_id", UUID(as_uuid=True), nullable=False),
        sa.Column("signal_type", sa.String(20), nullable=False),
        sa.Column("strength", sa.Integer, nullable=False),
        sa.Column("strategy_contributions", JSONB),
        sa.Column("quorum_approved", sa.Boolean, server_default="false"),
        sa.Column("risk_manager_veto", sa.Boolean, server_default="false"),
        sa.Column("analyst_report_id", UUID(as_uuid=True)),
        sa.Column("quant_agent_id", sa.String(50)),
        sa.Column("timeframe", sa.String(10)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("time", "coin_id"),
    )
    op.execute("SELECT create_hypertable('signals', 'time', if_not_exists => TRUE)")

    # ── 7. wallets ──
    op.create_table(
        "wallets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("exchange_name", sa.String(50), nullable=False),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("total_balance", sa.Float, nullable=False),
        sa.Column("available_balance", sa.Float, nullable=False),
        sa.Column("frozen_balance", sa.Float, server_default="0"),
        sa.Column("reserve_balance", sa.Float, nullable=False),
        sa.Column("available_for_trading", sa.Float),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("exchange_name", "asset", name="uq_wallet_exchange_asset"),
    )

    # ── 8. agent_decisions (감사 추적) ──
    op.create_table(
        "agent_decisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_type", sa.String(50), nullable=False),
        sa.Column("agent_id", sa.String(50)),
        sa.Column("decision_type", sa.String(100), nullable=False),
        sa.Column("decision_outcome", sa.String(50), nullable=False),
        sa.Column("input_data", JSONB),
        sa.Column("output", JSONB, nullable=False),
        sa.Column("confidence_score", sa.Float),
        sa.Column("quorum_round_id", UUID(as_uuid=True)),
        sa.Column("performance_label", sa.String(50)),
        sa.Column("realized_pnl_usd", sa.Float),
        sa.Column("embedded", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_agent_decisions_type", "agent_decisions", ["agent_type"])
    op.create_index("idx_agent_decisions_created", "agent_decisions", [sa.text("created_at DESC")])

    # ── 9. config_changes (설정 변경 감사) ──
    op.create_table(
        "config_changes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("change_timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("changed_by", sa.String(100), nullable=False),
        sa.Column("config_key", sa.String(255), nullable=False),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text, nullable=False),
        sa.Column("risk_level", sa.String(20)),
        sa.Column("change_reason", sa.Text),
        sa.Column("validation_passed", sa.Boolean, server_default="true"),
        sa.Column("validation_errors", sa.Text),
        sa.Column("affected_agents", ARRAY(sa.String)),
        sa.Column("requires_confirmation", sa.Boolean, server_default="false"),
        sa.Column("confirmed", sa.Boolean, server_default="false"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("rollback_available", sa.Boolean, server_default="true"),
        sa.Column("rolled_back", sa.Boolean, server_default="false"),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_config_changes_key", "config_changes", ["config_key"])

    # ── 10. data_quarantine (P10 이상치 격리, hypertable) ──
    op.create_table(
        "data_quarantine",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coin_id", UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(50), nullable=False),
        sa.Column("raw_value", sa.Float, nullable=False),
        sa.Column("anomaly_method", sa.String(50), nullable=False),
        sa.Column("anomaly_score", sa.Float, nullable=False),
        sa.Column("threshold_exceeded", sa.Float, nullable=False),
        sa.Column("healing_applied", sa.Boolean, server_default="false"),
        sa.Column("healing_method", sa.String(50)),
        sa.Column("healed_value", sa.Float),
        sa.Column("window_size", sa.Integer),
        sa.Column("window_anomaly_ratio", sa.Float),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("time", "coin_id", "field_name"),
    )
    op.execute("SELECT create_hypertable('data_quarantine', 'time', if_not_exists => TRUE)")

    # ── 11. boot_state (P12 부트 시퀀스) ──
    op.create_table(
        "boot_state",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("boot_session_id", UUID(as_uuid=True), unique=True, nullable=False),
        sa.Column("boot_start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("boot_end_time", sa.DateTime(timezone=True)),
        sa.Column("boot_status", sa.String(50)),
        sa.Column("phase_0_infra_check", sa.Boolean, server_default="false"),
        sa.Column("phase_0_check_time", sa.DateTime(timezone=True)),
        sa.Column("phase_1_data_recovery", sa.Boolean, server_default="false"),
        sa.Column("phase_1_backfill_count", sa.Integer),
        sa.Column("phase_2_indicator_warmup", sa.Boolean, server_default="false"),
        sa.Column("phase_2_warmup_data", JSONB),
        sa.Column("phase_3_oms_sync", sa.Boolean, server_default="false"),
        sa.Column("phase_3_unexecuted_orders_count", sa.Integer),
        sa.Column("phase_4_health_check", sa.Boolean, server_default="false"),
        sa.Column("phase_4_agent_statuses", JSONB),
        sa.Column("phase_5_trading_enabled", sa.Boolean, server_default="false"),
        sa.Column("phase_5_enabled_strategies", ARRAY(sa.String)),
        sa.Column("total_boot_duration_ms", sa.Integer),
        sa.Column("errors", JSONB, server_default="'[]'"),
        sa.Column("system_version", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── 12. watchlist (일일 코인 선별) ──
    op.create_table(
        "watchlist",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("selection_date", sa.Date, nullable=False),
        sa.Column("coin_id", UUID(as_uuid=True), sa.ForeignKey("coins.id"), nullable=False),
        sa.Column("stage1_passed", sa.Boolean, nullable=False),
        sa.Column("stage1_score", sa.Integer),
        sa.Column("stage2_passed", sa.Boolean, nullable=False),
        sa.Column("selection_rank", sa.Integer, nullable=False),
        sa.Column("total_selected", sa.Integer, nullable=False),
        sa.Column("days_on_watchlist", sa.Integer, server_default="0"),
        sa.Column("is_whitelist", sa.Boolean, server_default="false"),
        sa.Column("is_token_unlock_warning", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("selection_date", "coin_id", name="uq_watchlist_date_coin"),
    )

    # ── 13. agent_memory_embeddings (P11 RAG, pgvector) ──
    op.create_table(
        "agent_memory_embeddings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_type", sa.String(50), nullable=False),
        sa.Column("memory_type", sa.String(50), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", JSONB, server_default="'{}'"),
        sa.Column("relevance_score", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    # pgvector 임베딩 컬럼 추가 (vector 타입)
    op.execute(
        "ALTER TABLE agent_memory_embeddings "
        "ADD COLUMN embedding vector(768) NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_memory_embedding_vector "
        "ON agent_memory_embeddings "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.create_index("idx_memory_agent_type", "agent_memory_embeddings", ["agent_type"])


def downgrade() -> None:
    op.drop_table("agent_memory_embeddings")
    op.drop_table("watchlist")
    op.drop_table("boot_state")
    op.drop_table("data_quarantine")
    op.drop_table("config_changes")
    op.drop_table("agent_decisions")
    op.drop_table("wallets")
    op.drop_table("signals")
    op.drop_table("trades")
    op.drop_table("positions")
    op.drop_table("orders")
    op.drop_table("market_data")
    op.drop_table("coins")
    op.execute("DROP EXTENSION IF EXISTS vector")
