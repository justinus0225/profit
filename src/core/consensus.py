"""합의 메커니즘 관리 모듈.

ARCHITECTURE.md Section 5: 합의 프로토콜
4단계 합의 프로세스:
1. 제안(Proposal): Quant → Orchestrator
2. 독립 검증(Independent Validation): Analyst + Risk Manager
3. 정합성 검증(Reconciliation): 코사인 유사도 측정
4. 최종 승인(Final Approval): 2-out-of-3 쿼럼 + Risk Manager 거부권

ConsensusManager는 합의 라운드의 생명주기를 관리하고
영속 기록과 메트릭을 제공한다.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ConsensusResult(str, Enum):
    """합의 라운드 결과."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


class RejectionReason(str, Enum):
    """거부 사유 분류."""
    RISK_VETO = "risk_veto"
    DIRECTION_MISMATCH = "direction_mismatch"
    QUORUM_NOT_MET = "quorum_not_met"
    LLM_REJECTION = "llm_rejection"
    TIMEOUT = "timeout"


@dataclass
class Vote:
    """에이전트 투표."""
    agent_type: str
    approved: bool
    confidence: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class ConsensusRound:
    """단일 합의 라운드 상태 추적.

    기존 OrchestratorAgent의 ConsensusRound를 독립 모듈로 추출.
    영속 기록과 메트릭을 위한 구조화된 데이터를 제공한다.
    """

    signal: dict[str, Any]
    round_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)

    # 투표
    quant_vote: Vote | None = None
    analyst_vote: Vote | None = None
    risk_vote: Vote | None = None

    # 리스크 거부권
    risk_veto: bool = False

    # 결과
    result: ConsensusResult = ConsensusResult.PENDING
    rejection_reason: RejectionReason | None = None
    rejection_detail: str = ""

    # 유사도 + 최종 판단
    cosine_similarity: float = 0.0
    final_decision: dict[str, Any] = field(default_factory=dict)

    # 타이밍
    resolved_at: float | None = None

    @property
    def signal_id(self) -> str:
        return self.signal.get("signal_id", "")

    @property
    def votes_collected(self) -> bool:
        return self.analyst_vote is not None and self.risk_vote is not None

    @property
    def vote_count(self) -> int:
        """승인 투표 수 (퀀트는 신호 발신자이므로 항상 YES)."""
        count = 1  # quant always votes yes
        if self.analyst_vote and self.analyst_vote.approved:
            count += 1
        if self.risk_vote and self.risk_vote.approved:
            count += 1
        return count

    @property
    def duration_ms(self) -> int | None:
        if self.resolved_at is None:
            return None
        return int((self.resolved_at - self.created_at) * 1000)

    def is_expired(self, timeout_seconds: float = 120) -> bool:
        return time.time() - self.created_at > timeout_seconds

    def to_record(self) -> dict[str, Any]:
        """영속 저장용 직렬화."""
        return {
            "round_id": self.round_id,
            "signal_id": self.signal_id,
            "symbol": self.signal.get("symbol"),
            "direction": self.signal.get("direction"),
            "signal_score": self.signal.get("signal_score"),
            "quant_vote": self.quant_vote.approved if self.quant_vote else True,
            "analyst_vote": self.analyst_vote.approved if self.analyst_vote else None,
            "analyst_confidence": self.analyst_vote.confidence if self.analyst_vote else None,
            "risk_vote": self.risk_vote.approved if self.risk_vote else None,
            "risk_veto": self.risk_veto,
            "vote_count": self.vote_count,
            "cosine_similarity": self.cosine_similarity,
            "result": self.result.value,
            "rejection_reason": self.rejection_reason.value if self.rejection_reason else None,
            "rejection_detail": self.rejection_detail,
            "duration_ms": self.duration_ms,
            "created_at": datetime.fromtimestamp(self.created_at, tz=timezone.utc).isoformat(),
            "resolved_at": (
                datetime.fromtimestamp(self.resolved_at, tz=timezone.utc).isoformat()
                if self.resolved_at else None
            ),
        }


class ConsensusMetrics:
    """합의 메커니즘 누적 메트릭."""

    def __init__(self) -> None:
        self.total_rounds: int = 0
        self.approved_count: int = 0
        self.rejected_count: int = 0
        self.timeout_count: int = 0
        self.veto_count: int = 0
        self.direction_mismatch_count: int = 0
        self.quorum_fail_count: int = 0
        self.total_duration_ms: int = 0
        self._history: list[dict[str, Any]] = []

    @property
    def approval_rate(self) -> float:
        if self.total_rounds == 0:
            return 0.0
        return self.approved_count / self.total_rounds

    @property
    def avg_duration_ms(self) -> float:
        completed = self.approved_count + self.rejected_count
        if completed == 0:
            return 0.0
        return self.total_duration_ms / completed

    def record(self, round_: ConsensusRound) -> None:
        """라운드 결과를 메트릭에 반영."""
        self.total_rounds += 1

        if round_.result == ConsensusResult.APPROVED:
            self.approved_count += 1
        elif round_.result == ConsensusResult.REJECTED:
            self.rejected_count += 1
            if round_.rejection_reason == RejectionReason.RISK_VETO:
                self.veto_count += 1
            elif round_.rejection_reason == RejectionReason.DIRECTION_MISMATCH:
                self.direction_mismatch_count += 1
            elif round_.rejection_reason == RejectionReason.QUORUM_NOT_MET:
                self.quorum_fail_count += 1
        elif round_.result == ConsensusResult.TIMEOUT:
            self.timeout_count += 1

        if round_.duration_ms is not None:
            self.total_duration_ms += round_.duration_ms

        record = round_.to_record()
        self._history.append(record)

        # 최근 100건만 메모리에 보관
        if len(self._history) > 100:
            self._history = self._history[-100:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_rounds": self.total_rounds,
            "approved": self.approved_count,
            "rejected": self.rejected_count,
            "timeout": self.timeout_count,
            "veto": self.veto_count,
            "direction_mismatch": self.direction_mismatch_count,
            "quorum_fail": self.quorum_fail_count,
            "approval_rate": round(self.approval_rate, 3),
            "avg_duration_ms": round(self.avg_duration_ms, 1),
        }


class ConsensusManager:
    """합의 프로토콜 관리자.

    오케스트레이터가 사용하는 합의 라운드의 생명주기를 관리한다:
    - 라운드 생성/조회
    - 투표 등록
    - 합의 판정 (코사인 유사도 + 쿼럼 + 거부권)
    - 만료 라운드 정리
    - 메트릭 수집
    """

    def __init__(
        self,
        quorum_required: int = 2,
        similarity_min: float = 0.60,
        round_timeout_seconds: float = 120,
    ) -> None:
        self._quorum_required = quorum_required
        self._similarity_min = similarity_min
        self._round_timeout = round_timeout_seconds
        self._active_rounds: dict[str, ConsensusRound] = {}
        self.metrics = ConsensusMetrics()

    @property
    def active_round_count(self) -> int:
        return len(self._active_rounds)

    def create_round(self, signal: dict[str, Any]) -> ConsensusRound:
        """새 합의 라운드 생성."""
        signal_id = signal.get("signal_id", "")
        if not signal_id:
            raise ValueError("signal_id is required")

        round_ = ConsensusRound(signal=signal)

        # 퀀트는 신호 발신자이므로 자동 승인
        round_.quant_vote = Vote(
            agent_type="quant",
            approved=True,
            confidence=signal.get("signal_score", 0) / 100.0,
            data=signal,
        )

        self._active_rounds[signal_id] = round_

        logger.info(
            "Consensus round created: %s %s (score=%s)",
            signal.get("direction"),
            signal.get("symbol"),
            signal.get("signal_score"),
        )
        return round_

    def get_round(self, signal_id: str) -> ConsensusRound | None:
        """진행 중 라운드 조회."""
        return self._active_rounds.get(signal_id)

    def register_analyst_vote(
        self, signal_id: str, approved: bool, data: dict[str, Any]
    ) -> ConsensusRound | None:
        """애널리스트 투표 등록."""
        round_ = self._active_rounds.get(signal_id)
        if not round_ or round_.result != ConsensusResult.PENDING:
            return None

        round_.analyst_vote = Vote(
            agent_type="analyst",
            approved=approved,
            confidence=data.get("fundamental_score", 0) / 100.0,
            data=data,
        )

        logger.info(
            "Analyst vote: %s (direction=%.2f, score=%s)",
            "YES" if approved else "NO",
            data.get("market_direction_score", 0),
            data.get("fundamental_score", 0),
        )
        return round_

    def register_risk_vote(
        self, signal_id: str, approved: bool, data: dict[str, Any]
    ) -> ConsensusRound | None:
        """리스크 매니저 투표 등록."""
        round_ = self._active_rounds.get(signal_id)
        if not round_ or round_.result != ConsensusResult.PENDING:
            return None

        round_.risk_vote = Vote(
            agent_type="risk",
            approved=approved,
            confidence=1.0 - (data.get("risk_score", 0) / 100.0),
            data=data,
        )
        round_.risk_veto = data.get("veto_flag", False)

        logger.info(
            "Risk vote: %s (veto=%s, level=%s)",
            "YES" if approved else "NO",
            round_.risk_veto,
            data.get("risk_level"),
        )
        return round_

    def evaluate(self, round_: ConsensusRound) -> ConsensusResult:
        """합의 판정 (코사인 유사도 + 쿼럼 + 거부권).

        Returns:
            ConsensusResult: 판정 결과. LLM 최종 판단이 필요한 경우
            PENDING을 반환하지 않고 APPROVED를 반환한다
            (호출 측에서 LLM 판단 후 최종 결정).
        """
        if not round_.votes_collected:
            return ConsensusResult.PENDING

        # 1. Risk Manager 거부권 체크 (최우선)
        if round_.risk_veto:
            self._reject(
                round_,
                RejectionReason.RISK_VETO,
                f"Risk Manager VETO: {round_.risk_vote.data.get('rejection_reason', '') if round_.risk_vote else ''}",
            )
            return ConsensusResult.REJECTED

        # 2. 코사인 유사도 체크
        quant_direction = 1.0 if round_.signal.get("direction") == "BUY" else -1.0
        analyst_direction = (
            round_.analyst_vote.data.get("market_direction_score", 0)
            if round_.analyst_vote
            else 0
        )
        similarity = self.cosine_similarity(quant_direction, analyst_direction)
        round_.cosine_similarity = similarity

        if similarity < self._similarity_min:
            self._reject(
                round_,
                RejectionReason.DIRECTION_MISMATCH,
                f"Direction mismatch: similarity={similarity:.2f} < {self._similarity_min}",
            )
            return ConsensusResult.REJECTED

        # 3. 2-out-of-3 쿼럼 체크
        if round_.vote_count < self._quorum_required:
            self._reject(
                round_,
                RejectionReason.QUORUM_NOT_MET,
                f"Quorum not met: {round_.vote_count}/{self._quorum_required}",
            )
            return ConsensusResult.REJECTED

        # 4. 합의 달성 → 호출 측에서 LLM 최종 판단 수행
        return ConsensusResult.APPROVED

    def finalize_approval(
        self, round_: ConsensusRound, decision: dict[str, Any]
    ) -> None:
        """LLM 최종 승인 확정."""
        round_.result = ConsensusResult.APPROVED
        round_.final_decision = decision
        round_.resolved_at = time.time()
        self.metrics.record(round_)
        self._active_rounds.pop(round_.signal_id, None)

    def finalize_rejection(
        self, round_: ConsensusRound, reason: str
    ) -> None:
        """LLM 최종 거부 확정."""
        round_.result = ConsensusResult.REJECTED
        round_.rejection_reason = RejectionReason.LLM_REJECTION
        round_.rejection_detail = reason
        round_.resolved_at = time.time()
        self.metrics.record(round_)
        self._active_rounds.pop(round_.signal_id, None)

    def expire_round(self, signal_id: str) -> ConsensusRound | None:
        """타임아웃 라운드 만료 처리."""
        round_ = self._active_rounds.pop(signal_id, None)
        if round_ and round_.result == ConsensusResult.PENDING:
            round_.result = ConsensusResult.TIMEOUT
            round_.rejection_reason = RejectionReason.TIMEOUT
            round_.rejection_detail = "Consensus timeout"
            round_.resolved_at = time.time()
            self.metrics.record(round_)
            logger.warning("Consensus round timeout: %s", signal_id)
        return round_

    def cleanup_expired(self) -> list[ConsensusRound]:
        """만료된 라운드를 모두 정리하고 반환."""
        expired_ids = [
            sid
            for sid, r in self._active_rounds.items()
            if r.is_expired(self._round_timeout)
        ]
        expired_rounds = []
        for sid in expired_ids:
            round_ = self.expire_round(sid)
            if round_:
                expired_rounds.append(round_)
        return expired_rounds

    def _reject(
        self, round_: ConsensusRound, reason: RejectionReason, detail: str
    ) -> None:
        """라운드 거부 처리 (내부용)."""
        round_.result = ConsensusResult.REJECTED
        round_.rejection_reason = reason
        round_.rejection_detail = detail
        round_.resolved_at = time.time()
        self.metrics.record(round_)
        self._active_rounds.pop(round_.signal_id, None)

    @staticmethod
    def cosine_similarity(quant_direction: float, analyst_score: float) -> float:
        """방향성 유사도 계산.

        퀀트 방향(+1/-1)과 애널리스트 시장 방향 점수(-1.0~+1.0)의
        정규화된 유사도를 반환한다 (0.0~1.0).
        """
        if quant_direction == 0 or analyst_score == 0:
            return 0.0
        sign_match = (quant_direction > 0) == (analyst_score > 0)
        strength = abs(analyst_score)
        if sign_match:
            return 0.5 + strength * 0.5
        return 0.5 - strength * 0.5
