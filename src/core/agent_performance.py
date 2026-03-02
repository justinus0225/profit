"""에이전트 성과 추적 및 동적 가중치 (ARCHITECTURE.md P4).

에이전트별 과거 판단의 정확도를 추적하고,
합의 투표 시 가중치를 동적으로 조정한다.

추적 지표:
- Quant: 시그널 스코어 vs 실제 수익률 상관계수
- Analyst: 시장 방향 예측 정확도
- Risk: 리스크 경고 적중률

가중치는 EMA(지수이동평균) 기반으로 최근 성과에 더 높은 비중을 부여한다.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# 기본 가중치 (모든 에이전트 동일 시작)
DEFAULT_WEIGHT = 1.0
MIN_WEIGHT = 0.3
MAX_WEIGHT = 2.0

# EMA 평활 상수 (작을수록 과거 비중 높음)
EMA_ALPHA = 0.1

# 성과 평가에 필요한 최소 샘플 수
MIN_SAMPLES = 10

# Redis 키 접두사
REDIS_KEY_PREFIX = "agent:performance"


@dataclass
class AgentScorecard:
    """에이전트별 성과 스코어카드."""

    agent_type: str
    total_decisions: int = 0
    correct_decisions: int = 0
    accuracy: float = 0.0  # 정확도 (0.0~1.0)
    ema_accuracy: float = 0.5  # EMA 기반 정확도
    consensus_weight: float = DEFAULT_WEIGHT  # 합의 가중치
    last_updated: float = 0.0
    recent_results: list[bool] = field(default_factory=list)


class AgentPerformanceTracker:
    """에이전트 성과 추적 + 동적 가중치 관리.

    Redis에 스코어카드를 저장하고, 매매 완료 후 실제 결과와 대조한다.
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client
        self._scorecards: dict[str, AgentScorecard] = {}

    async def load(self) -> None:
        """Redis에서 스코어카드를 로드한다."""
        data = await self._redis.hgetall(REDIS_KEY_PREFIX)
        for agent_type, raw in data.items():
            try:
                parsed = json.loads(raw)
                self._scorecards[agent_type] = AgentScorecard(
                    agent_type=agent_type,
                    total_decisions=parsed.get("total_decisions", 0),
                    correct_decisions=parsed.get("correct_decisions", 0),
                    accuracy=parsed.get("accuracy", 0.0),
                    ema_accuracy=parsed.get("ema_accuracy", 0.5),
                    consensus_weight=parsed.get("consensus_weight", DEFAULT_WEIGHT),
                    last_updated=parsed.get("last_updated", 0.0),
                    recent_results=parsed.get("recent_results", []),
                )
            except (json.JSONDecodeError, TypeError):
                continue

    async def save(self, agent_type: str) -> None:
        """스코어카드를 Redis에 저장한다."""
        sc = self._scorecards.get(agent_type)
        if not sc:
            return
        data = {
            "total_decisions": sc.total_decisions,
            "correct_decisions": sc.correct_decisions,
            "accuracy": round(sc.accuracy, 4),
            "ema_accuracy": round(sc.ema_accuracy, 4),
            "consensus_weight": round(sc.consensus_weight, 4),
            "last_updated": sc.last_updated,
            "recent_results": sc.recent_results[-100:],  # 최근 100건만 유지
        }
        await self._redis.hset(
            REDIS_KEY_PREFIX,
            agent_type,
            json.dumps(data),
        )

    async def record_outcome(
        self,
        agent_type: str,
        correct: bool,
    ) -> AgentScorecard:
        """에이전트 의사결정의 성과를 기록한다.

        Args:
            agent_type: 에이전트 유형 ("quant", "analyst", "risk")
            correct: 올바른 판단이었는지

        Returns:
            갱신된 AgentScorecard
        """
        sc = self._scorecards.get(agent_type)
        if not sc:
            sc = AgentScorecard(agent_type=agent_type)
            self._scorecards[agent_type] = sc

        sc.total_decisions += 1
        if correct:
            sc.correct_decisions += 1
        sc.recent_results.append(correct)
        sc.recent_results = sc.recent_results[-100:]

        # 정확도 계산
        sc.accuracy = sc.correct_decisions / sc.total_decisions

        # EMA 기반 가중 정확도 갱신
        outcome_val = 1.0 if correct else 0.0
        sc.ema_accuracy = EMA_ALPHA * outcome_val + (1 - EMA_ALPHA) * sc.ema_accuracy

        # 가중치 업데이트
        sc.consensus_weight = self._calculate_weight(sc)
        sc.last_updated = time.time()

        await self.save(agent_type)
        logger.info(
            "Performance updated: agent=%s, correct=%s, accuracy=%.2f, "
            "ema=%.2f, weight=%.2f",
            agent_type,
            correct,
            sc.accuracy,
            sc.ema_accuracy,
            sc.consensus_weight,
        )
        return sc

    def _calculate_weight(self, sc: AgentScorecard) -> float:
        """EMA 정확도 기반 가중치를 산출한다.

        가중치 공식: weight = clamp(ema_accuracy * 2, MIN_WEIGHT, MAX_WEIGHT)
        - ema_accuracy 0.5 → weight 1.0 (기본)
        - ema_accuracy 0.7 → weight 1.4 (우수)
        - ema_accuracy 0.3 → weight 0.6 (부진)
        """
        if sc.total_decisions < MIN_SAMPLES:
            return DEFAULT_WEIGHT
        raw = sc.ema_accuracy * 2
        return max(MIN_WEIGHT, min(MAX_WEIGHT, raw))

    def get_weight(self, agent_type: str) -> float:
        """에이전트의 현재 합의 가중치를 반환한다."""
        sc = self._scorecards.get(agent_type)
        if not sc or sc.total_decisions < MIN_SAMPLES:
            return DEFAULT_WEIGHT
        return sc.consensus_weight

    def get_scorecard(self, agent_type: str) -> AgentScorecard | None:
        """에이전트 스코어카드를 반환한다."""
        return self._scorecards.get(agent_type)

    def get_all_scorecards(self) -> dict[str, AgentScorecard]:
        """모든 에이전트 스코어카드를 반환한다."""
        return dict(self._scorecards)

    async def evaluate_trade(
        self,
        trade_result: dict[str, Any],
    ) -> None:
        """매매 완료 후 관련 에이전트들의 성과를 평가한다.

        Args:
            trade_result: 매매 결과
                - symbol: 코인 심볼
                - direction: "buy" | "sell"
                - pnl_pct: 수익률 (%)
                - quant_signal_score: 퀀트 시그널 스코어
                - analyst_direction: 분석가 예측 방향
                - risk_approved: 리스크 매니저 승인 여부
        """
        pnl_pct = trade_result.get("pnl_pct", 0)
        profitable = pnl_pct > 0

        # Quant: 시그널이 수익으로 이어졌는가
        signal_score = trade_result.get("quant_signal_score")
        if signal_score is not None:
            # 높은 스코어(>0.6)로 매수했는데 수익 → 정확
            quant_correct = (signal_score > 0.6 and profitable) or (
                signal_score <= 0.4 and not profitable
            )
            await self.record_outcome("quant", quant_correct)

        # Analyst: 방향 예측이 맞았는가
        analyst_dir = trade_result.get("analyst_direction")
        if analyst_dir is not None:
            direction = trade_result.get("direction", "buy")
            if direction == "buy":
                analyst_correct = analyst_dir == "bullish" and profitable
            else:
                analyst_correct = analyst_dir == "bearish" and profitable
            await self.record_outcome("analyst", analyst_correct)

        # Risk: 리스크 판단이 적절했는가
        risk_approved = trade_result.get("risk_approved")
        if risk_approved is not None:
            # 승인한 거래가 수익 → 정확, 거부한 거래가 수익이면 → 오판
            risk_correct = (risk_approved and profitable) or (
                not risk_approved and not profitable
            )
            await self.record_outcome("risk", risk_correct)
