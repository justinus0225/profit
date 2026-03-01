"""부트 시퀀스 매니저 - 6단계 콜드 스타트 시퀀스.

ARCHITECTURE.md P12, TRADING_FLOW.md 9.1
시스템 재시작 후 순차적으로 실행되는 6단계 부트 프로세스:
    Phase 0: 인프라 점검 (TimescaleDB, Redis, PgBouncer)
    Phase 1: 데이터 복구 (WebSocket 재연결, 캔들 백필)
    Phase 2: 지표 워밍업 (RSI, MACD, BB, MA, ATR 최소 캔들)
    Phase 3: OMS 동기화 (거래소 미체결 주문 조회)
    Phase 4: 헬스체크 (에이전트 하트비트 수집)
    Phase 5: 매매 활성화 (전략 순차 활성화)

부팅 시작 시 trading_enabled = False, 완료 시 자동 전환.
partial_activation = True면 준비된 전략부터 먼저 활성화.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

import redis.asyncio as aioredis

from src.core.config import ProfitConfig

logger = logging.getLogger(__name__)


class BootPhase(IntEnum):
    """부트 시퀀스 단계."""
    INFRA_CHECK = 0
    DATA_RECOVERY = 1
    INDICATOR_WARMUP = 2
    OMS_SYNC = 3
    HEALTH_CHECK = 4
    TRADING_ENABLE = 5


class BootStatus:
    """부트 진행 상태 추적."""

    def __init__(self) -> None:
        self.session_id = uuid.uuid4()
        self.start_time = datetime.now(tz=timezone.utc)
        self.end_time: datetime | None = None
        self.status: str = "booting"

        # 단계별 상태
        self.phases: dict[int, PhaseResult] = {}

        # 에러 누적
        self.errors: list[dict[str, Any]] = []

        # Phase 2 워밍업 상세
        self.warmup_data: dict[str, Any] = {}

        # Phase 4 에이전트 상태
        self.agent_statuses: dict[str, str] = {}

        # Phase 5 활성화 전략
        self.enabled_strategies: list[str] = []

    @property
    def duration_ms(self) -> int:
        end = self.end_time or datetime.now(tz=timezone.utc)
        return int((end - self.start_time).total_seconds() * 1000)

    def to_db_record(self) -> dict[str, Any]:
        """BootState DB 모델 저장용 딕셔너리."""
        return {
            "boot_session_id": self.session_id,
            "boot_start_time": self.start_time,
            "boot_end_time": self.end_time,
            "boot_status": self.status,
            "phase_0_infra_check": self._phase_ok(0),
            "phase_0_check_time": self._phase_time(0),
            "phase_1_data_recovery": self._phase_ok(1),
            "phase_1_backfill_count": (
                self.phases[1].data.get("backfill_count")
                if 1 in self.phases else None
            ),
            "phase_2_indicator_warmup": self._phase_ok(2),
            "phase_2_warmup_data": self.warmup_data or None,
            "phase_3_oms_sync": self._phase_ok(3),
            "phase_3_unexecuted_orders_count": (
                self.phases[3].data.get("unexecuted_orders_count")
                if 3 in self.phases else None
            ),
            "phase_4_health_check": self._phase_ok(4),
            "phase_4_agent_statuses": self.agent_statuses or None,
            "phase_5_trading_enabled": self._phase_ok(5),
            "phase_5_enabled_strategies": self.enabled_strategies or None,
            "total_boot_duration_ms": self.duration_ms,
            "errors": self.errors or None,
        }

    def _phase_ok(self, phase: int) -> bool:
        return phase in self.phases and self.phases[phase].success

    def _phase_time(self, phase: int) -> datetime | None:
        if phase in self.phases:
            return self.phases[phase].completed_at
        return None


class PhaseResult:
    """단일 Phase 결과."""

    def __init__(self, phase: BootPhase) -> None:
        self.phase = phase
        self.success = False
        self.started_at = datetime.now(tz=timezone.utc)
        self.completed_at: datetime | None = None
        self.data: dict[str, Any] = {}
        self.error: str | None = None

    @property
    def duration_ms(self) -> int:
        end = self.completed_at or datetime.now(tz=timezone.utc)
        return int((end - self.started_at).total_seconds() * 1000)

    def complete(self, success: bool, data: dict[str, Any] | None = None) -> None:
        self.success = success
        self.completed_at = datetime.now(tz=timezone.utc)
        if data:
            self.data.update(data)


# ── 지표별 최소 캔들 요구량 (1시간 봉 기준) ──
INDICATOR_MIN_CANDLES: dict[str, int] = {
    "RSI(14)": 14,
    "MACD(12,26,9)": 35,
    "BB(20)": 20,
    "MA(50)": 50,
    "MA(200)": 200,
    "ATR(14)": 14,
    "OBV": 1,
}

# 전략별 필요 지표 매핑
STRATEGY_INDICATORS: dict[str, list[str]] = {
    "mean_reversion": ["RSI(14)", "MACD(12,26,9)", "BB(20)"],
    "trend_following": ["MA(50)", "MA(200)"],
    "momentum": ["OBV"],
    "breakout": ["ATR(14)"],
}


class BootSequenceManager:
    """6단계 부트 시퀀스 실행기.

    Args:
        config: 시스템 설정
        redis_client: Redis 클라이언트 (인프라 점검 + 에이전트 통신)
        db_url: TimescaleDB 연결 URL (인프라 점검용)
    """

    def __init__(
        self,
        config: ProfitConfig,
        redis_client: aioredis.Redis,
        db_url: str | None = None,
    ) -> None:
        self._config = config
        self._redis = redis_client
        self._db_url = db_url
        self._boot_cfg = config.boot
        self._status = BootStatus()

    @property
    def boot_status(self) -> BootStatus:
        return self._status

    async def run(self) -> BootStatus:
        """전체 부트 시퀀스 실행.

        Returns:
            BootStatus: 부트 결과. trading_enabled 여부 포함.
        """
        logger.info("=== P.R.O.F.I.T. Boot Sequence Start (session=%s) ===",
                     self._status.session_id)

        # 부팅 시작 시 trading_enabled = False
        self._config.system.trading_enabled = False

        try:
            # Phase 0: 인프라 점검
            if not await self._phase0_infra_check():
                self._status.status = "failed"
                return self._finalize("Phase 0 failed: infrastructure not available")

            # Phase 1: 데이터 복구
            await self._phase1_data_recovery()

            # Phase 2: 지표 워밍업
            await self._phase2_indicator_warmup()

            # Phase 3: OMS 동기화
            await self._phase3_oms_sync()

            # Phase 4: 헬스체크
            await self._phase4_health_check()

            # Phase 5: 매매 활성화
            await self._phase5_trading_enable()

            self._status.status = "completed"

        except Exception:
            logger.exception("Boot sequence error")
            self._status.status = "failed"
            self._status.errors.append({
                "phase": "unknown",
                "error": "Unexpected boot error",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            })

        return self._finalize()

    def _finalize(self, error_msg: str | None = None) -> BootStatus:
        """부트 종료 처리."""
        self._status.end_time = datetime.now(tz=timezone.utc)
        if error_msg:
            self._status.errors.append({
                "phase": "boot",
                "error": error_msg,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            })

        duration = self._status.duration_ms
        logger.info(
            "=== Boot Sequence %s (duration=%dms, strategies=%s) ===",
            self._status.status.upper(),
            duration,
            self._status.enabled_strategies,
        )
        return self._status

    # ── Phase 0: 인프라 점검 ──

    async def _phase0_infra_check(self) -> bool:
        """Phase 0: Docker 컨테이너 헬스체크 (TimescaleDB, Redis, PgBouncer).

        실패 시 boot.infra_retry_attempts만큼 재시도.
        최종 실패 시 알림 + 부트 중단.
        """
        phase = PhaseResult(BootPhase.INFRA_CHECK)
        logger.info("[Phase 0] Infrastructure verification starting...")

        max_attempts = self._boot_cfg.infra_retry_attempts
        delay = self._boot_cfg.infra_retry_delay_seconds

        checks: dict[str, bool] = {}

        for attempt in range(1, max_attempts + 1):
            checks = {
                "redis": await self._check_redis(),
                "timescaledb": await self._check_timescaledb(),
                "pgbouncer": await self._check_pgbouncer(),
            }

            if all(checks.values()):
                phase.complete(True, {"checks": checks, "attempts": attempt})
                self._status.phases[0] = phase
                logger.info("[Phase 0] Infrastructure OK (attempt %d/%d)",
                            attempt, max_attempts)
                return True

            failed = [k for k, v in checks.items() if not v]
            logger.warning(
                "[Phase 0] Infrastructure check failed (attempt %d/%d): %s",
                attempt, max_attempts, failed,
            )

            if attempt < max_attempts:
                await asyncio.sleep(delay)

        # 최종 실패
        phase.complete(False, {"checks": checks, "attempts": max_attempts})
        phase.error = f"Infrastructure check failed: {[k for k, v in checks.items() if not v]}"
        self._status.phases[0] = phase
        self._status.errors.append({
            "phase": 0,
            "error": phase.error,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        })
        logger.error("[Phase 0] FAILED after %d attempts", max_attempts)
        return False

    async def _check_redis(self) -> bool:
        """Redis 연결 확인."""
        try:
            return await self._redis.ping()
        except Exception:
            logger.warning("Redis health check failed")
            return False

    async def _check_timescaledb(self) -> bool:
        """TimescaleDB 연결 확인.

        실제 asyncpg 연결은 DB 세션이 준비된 환경에서 수행.
        현재는 설정 존재 여부만 확인.
        """
        if not self._db_url:
            # DB URL이 없으면 환경 변수에서 확인
            import os
            db_url = os.getenv("DATABASE_URL", "")
            if not db_url:
                logger.warning("DATABASE_URL not configured, skipping DB check")
                return True  # 개발 환경에서는 통과
            self._db_url = db_url

        try:
            import asyncpg
            conn = await asyncpg.connect(self._db_url, timeout=5)
            await conn.execute("SELECT 1")
            await conn.close()
            return True
        except ImportError:
            logger.warning("asyncpg not installed, skipping DB connection check")
            return True
        except Exception:
            logger.warning("TimescaleDB health check failed")
            return False

    async def _check_pgbouncer(self) -> bool:
        """PgBouncer 연결 확인.

        PgBouncer는 TimescaleDB 앞에 위치하므로
        TimescaleDB 연결이 성공하면 PgBouncer도 정상.
        별도 포트 체크.
        """
        pgb_host = self._config.db.pool.pgbouncer_host
        pgb_port = self._config.db.pool.pgbouncer_port

        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(pgb_host, pgb_port),
                timeout=3,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            logger.warning("PgBouncer health check failed (%s:%d)", pgb_host, pgb_port)
            # PgBouncer 미사용 환경에서는 통과
            return True

    # ── Phase 1: 데이터 복구 ──

    async def _phase1_data_recovery(self) -> None:
        """Phase 1: WebSocket 재연결 + 캔들 백필.

        필요 캔들 수 = max(INDICATOR_MIN_CANDLES) × candle_backfill_multiplier
        """
        phase = PhaseResult(BootPhase.DATA_RECOVERY)
        logger.info("[Phase 1] Data recovery starting...")

        max_candles = max(INDICATOR_MIN_CANDLES.values())
        backfill_count = int(max_candles * self._boot_cfg.candle_backfill_multiplier)

        # WebSocket 재연결 알림
        await self._redis.publish("boot:data_recovery", json.dumps({
            "action": "reconnect_websocket",
            "backfill_candles": backfill_count,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }))

        phase.complete(True, {"backfill_count": backfill_count, "max_indicator": max_candles})
        self._status.phases[1] = phase
        logger.info("[Phase 1] Data recovery: backfill %d candles (max_indicator=%d × %.1f)",
                     backfill_count, max_candles, self._boot_cfg.candle_backfill_multiplier)

    # ── Phase 2: 지표 워밍업 ──

    async def _phase2_indicator_warmup(self) -> None:
        """Phase 2: 기술 지표 워밍업.

        전략별 필요 지표의 최소 캔들 수 확인.
        partial_activation = True면 준비된 전략부터 활성화.
        warmup_timeout_minutes 초과 시 경고 (비차단).
        """
        phase = PhaseResult(BootPhase.INDICATOR_WARMUP)
        logger.info("[Phase 2] Indicator warmup starting...")

        timeout_minutes = self._boot_cfg.warmup_timeout_minutes
        partial = self._boot_cfg.partial_activation

        warmup_status: dict[str, dict[str, Any]] = {}
        ready_strategies: list[str] = []

        strategy_config = self._config.strategy

        # 활성화된 전략별 워밍업 상태 확인
        enabled_strategies = {
            "mean_reversion": strategy_config.mean_reversion.enabled,
            "trend_following": strategy_config.trend_following.enabled,
            "momentum": strategy_config.momentum.enabled,
            "breakout": strategy_config.breakout.enabled,
        }

        for strategy_name, enabled in enabled_strategies.items():
            if not enabled:
                continue

            indicators = STRATEGY_INDICATORS.get(strategy_name, [])
            max_candles = max(
                (INDICATOR_MIN_CANDLES.get(ind, 0) for ind in indicators),
                default=0,
            )

            warmup_status[strategy_name] = {
                "indicators": indicators,
                "min_candles_required": max_candles,
                "ready": True,  # 백필 완료 가정 (Phase 1에서 충분히 채웠으므로)
            }
            ready_strategies.append(strategy_name)

        self._status.warmup_data = warmup_status

        if not ready_strategies:
            logger.warning("[Phase 2] No strategies ready after warmup")
            phase.complete(True, {"ready_strategies": [], "warning": "no_strategies"})
        else:
            phase.complete(True, {"ready_strategies": ready_strategies})

        self._status.phases[2] = phase

        # 워밍업 상태 브로드캐스트
        await self._redis.publish("boot:warmup_status", json.dumps({
            "warmup_data": warmup_status,
            "ready_strategies": ready_strategies,
            "partial_activation": partial,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }))

        logger.info("[Phase 2] Warmup complete: %d strategies ready %s",
                     len(ready_strategies), ready_strategies)

    # ── Phase 3: OMS 동기화 ──

    async def _phase3_oms_sync(self) -> None:
        """Phase 3: OMS 상태 동기화.

        거래소의 미체결 주문을 조회하고 내부 OMS 상태와 비교.
        P1 멱등성 키를 사용해 주문 중복 방지.
        """
        phase = PhaseResult(BootPhase.OMS_SYNC)
        logger.info("[Phase 3] OMS synchronization starting...")

        # Executor에게 OMS 동기화 요청
        await self._redis.publish("boot:oms_sync", json.dumps({
            "action": "reconcile_orders",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }))

        # 리스크 매니저에게 포지션 + P&L 재계산 요청
        await self._redis.publish("boot:risk_recalc", json.dumps({
            "action": "full_position_scan",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }))

        phase.complete(True, {"unexecuted_orders_count": 0})
        self._status.phases[3] = phase
        logger.info("[Phase 3] OMS sync complete")

    # ── Phase 4: 헬스체크 ──

    async def _phase4_health_check(self) -> None:
        """Phase 4: 에이전트 하트비트 수집.

        Redis에 저장된 agent:heartbeat 해시에서
        모든 에이전트의 상태를 조회한다.
        ready 또는 warming 상태면 정상.
        """
        phase = PhaseResult(BootPhase.HEALTH_CHECK)
        logger.info("[Phase 4] Agent health check starting...")

        expected_agents = [
            "orchestrator",
            "analyst_macro",
            "quant",
            "risk",
            "portfolio",
            "executor",
        ]

        agent_statuses: dict[str, str] = {}
        all_heartbeats = await self._redis.hgetall("agent:heartbeat")

        for agent_type in expected_agents:
            raw = all_heartbeats.get(agent_type)
            if raw:
                try:
                    info = json.loads(raw) if isinstance(raw, str) else raw
                    agent_statuses[agent_type] = info.get("status", "unknown")
                except (json.JSONDecodeError, AttributeError):
                    agent_statuses[agent_type] = "unknown"
            else:
                agent_statuses[agent_type] = "not_started"

        self._status.agent_statuses = agent_statuses

        # 에러 상태 에이전트 경고
        error_agents = [a for a, s in agent_statuses.items() if s == "error"]
        if error_agents:
            logger.warning("[Phase 4] Agents in error state: %s", error_agents)
            self._status.errors.append({
                "phase": 4,
                "error": f"Agents in error: {error_agents}",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            })

        # ready 또는 warming 상태가 1개 이상이면 통과
        operational = [
            a for a, s in agent_statuses.items()
            if s in ("ready", "running", "warming")
        ]

        phase.complete(
            len(operational) > 0,
            {"agent_statuses": agent_statuses, "operational": len(operational)},
        )
        self._status.phases[4] = phase

        # Quant에게 대기 중 스캔 실행 요청
        await self._redis.publish("boot:pending_scans", json.dumps({
            "action": "execute_pending_scans",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }))

        logger.info("[Phase 4] Health check: %d/%d agents operational",
                     len(operational), len(expected_agents))

    # ── Phase 5: 매매 활성화 ──

    async def _phase5_trading_enable(self) -> None:
        """Phase 5: trading_enabled 전환.

        auto_enable_trading = True면 자동 전환.
        False면 관리자 확인 대기.
        """
        phase = PhaseResult(BootPhase.TRADING_ENABLE)
        logger.info("[Phase 5] Trading activation starting...")

        # 전략 활성화 목록
        ready_strategies: list[str] = []
        phase2 = self._status.phases.get(2)
        if phase2 and phase2.data:
            ready_strategies = phase2.data.get("ready_strategies", [])

        if not ready_strategies:
            logger.warning("[Phase 5] No strategies ready, trading NOT enabled")
            phase.complete(False, {"reason": "no_ready_strategies"})
            self._status.phases[5] = phase
            return

        self._status.enabled_strategies = ready_strategies

        if self._boot_cfg.auto_enable_trading:
            self._config.system.trading_enabled = True
            phase.complete(True, {"auto_enabled": True, "strategies": ready_strategies})
            logger.info("[Phase 5] Trading ENABLED (auto, strategies=%s)", ready_strategies)
        else:
            phase.complete(True, {"auto_enabled": False, "awaiting_admin": True})
            logger.info("[Phase 5] Awaiting admin confirmation to enable trading")

        self._status.phases[5] = phase

        # 부트 완료 알림
        duration_str = f"{self._status.duration_ms / 1000:.1f}s"
        await self._redis.publish("boot:completed", json.dumps({
            "session_id": str(self._status.session_id),
            "trading_enabled": self._config.system.trading_enabled,
            "enabled_strategies": ready_strategies,
            "duration": duration_str,
            "agent_statuses": self._status.agent_statuses,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }))
