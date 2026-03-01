"""오케스트레이터 에이전트 - 합의 조율, 최종 의사결정.

ARCHITECTURE.md: Level 3, Orchestrator / CTO
- 2-out-of-3 Quorum 합의 프로토콜 실행
- Risk Manager 거부권 처리
- 신호 → 검증 → 합의 → 실행 워크플로우 조율
- 코사인 유사도 기반 방향성 일치 검증
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.core.llm.client import Message, Role

logger = logging.getLogger(__name__)


class ConsensusRound:
    """단일 합의 라운드 상태 추적."""

    def __init__(self, signal: dict[str, Any]) -> None:
        self.round_id = str(uuid.uuid4())
        self.signal = signal
        self.signal_id = signal.get("signal_id", "")
        self.created_at = time.time()

        # 투표 결과
        self.quant_vote: bool = True  # 신호 발신자이므로 기본 승인
        self.analyst_vote: bool | None = None
        self.analyst_response: dict[str, Any] = {}
        self.risk_vote: bool | None = None
        self.risk_response: dict[str, Any] = {}
        self.risk_veto: bool = False

        self.resolved = False
        self.result: str = ""  # "approved" | "rejected"
        self.rejection_reason: str = ""

    @property
    def votes_collected(self) -> bool:
        return self.analyst_vote is not None and self.risk_vote is not None

    @property
    def vote_count(self) -> int:
        count = 1  # quant always votes yes
        if self.analyst_vote:
            count += 1
        if self.risk_vote:
            count += 1
        return count

    def is_expired(self, timeout_seconds: float = 120) -> bool:
        return time.time() - self.created_at > timeout_seconds


class OrchestratorAgent(BaseAgent):
    """오케스트레이터: 합의 프로토콜 + 워크플로우 조율."""

    @property
    def agent_type(self) -> str:
        return "orchestrator"

    async def _on_initialize(self) -> None:
        self._signal_cfg = self._config.signal
        self._quorum_required = self._signal_cfg.consensus_quorum
        self._similarity_min = self._signal_cfg.consensus_similarity_min

        # 진행 중 합의 라운드
        self._active_rounds: dict[str, ConsensusRound] = {}

        # 이벤트 구독
        await self._subscribe("quant:signal", self._on_quant_signal)
        await self._subscribe("analyst:approval_response", self._on_analyst_response)
        await self._subscribe("risk:approval_response", self._on_risk_response)

    async def _on_run(self) -> None:
        """타임아웃 라운드 정리 루프."""
        while self._running:
            await self._cleanup_expired_rounds()
            await asyncio.sleep(10)

    # ── 합의 프로토콜 ──

    async def _on_quant_signal(self, data: dict[str, Any]) -> None:
        """Step 1: 퀀트 신호 수신 → 합의 라운드 시작."""
        signal_id = data.get("signal_id", "")
        if not signal_id:
            return

        round_ = ConsensusRound(data)
        self._active_rounds[signal_id] = round_

        logger.info("[%s] Consensus round started: %s %s (score=%s)",
                     self.name, data.get("direction"), data.get("symbol"),
                     data.get("signal_score"))

        # Step 2: 애널리스트와 리스크 매니저에게 독립 검증 요청
        await self._publish("orchestrator:consensus_check", {
            "signal_id": signal_id,
            "symbol": data.get("symbol"),
            "direction": data.get("direction"),
            "signal_score": data.get("signal_score"),
            "entry_price": data.get("entry_price"),
            "target_price": data.get("target_price"),
            "stop_loss_price": data.get("stop_loss_price"),
            "holding_period": data.get("holding_period"),
        })

        # 애널리스트에게도 별도 분석 요청
        await self._publish("orchestrator:analysis_request", {
            "signal_id": signal_id,
            "symbol": data.get("symbol"),
            "coin_id": data.get("coin_id"),
        })

    async def _on_analyst_response(self, data: dict[str, Any]) -> None:
        """애널리스트 검증 응답 수신."""
        signal_id = data.get("signal_id", "")
        round_ = self._active_rounds.get(signal_id)
        if not round_ or round_.resolved:
            return

        round_.analyst_vote = data.get("approval", False)
        round_.analyst_response = data

        logger.info("[%s] Analyst vote: %s (direction=%.2f, score=%s)",
                     self.name,
                     "YES" if round_.analyst_vote else "NO",
                     data.get("market_direction_score", 0),
                     data.get("fundamental_score", 0))

        if round_.votes_collected:
            await self._resolve_consensus(round_)

    async def _on_risk_response(self, data: dict[str, Any]) -> None:
        """리스크 매니저 검증 응답 수신."""
        signal_id = data.get("signal_id", "")
        round_ = self._active_rounds.get(signal_id)
        if not round_ or round_.resolved:
            return

        round_.risk_vote = data.get("approval", False)
        round_.risk_veto = data.get("veto_flag", False)
        round_.risk_response = data

        logger.info("[%s] Risk vote: %s (veto=%s, level=%s)",
                     self.name,
                     "YES" if round_.risk_vote else "NO",
                     round_.risk_veto,
                     data.get("risk_level"))

        if round_.votes_collected:
            await self._resolve_consensus(round_)

    async def _resolve_consensus(self, round_: ConsensusRound) -> None:
        """Step 3-4: 합의 결과 판정."""
        round_.resolved = True
        signal = round_.signal
        signal_id = round_.signal_id

        # Risk Manager 거부권 체크
        if round_.risk_veto:
            round_.result = "rejected"
            round_.rejection_reason = f"Risk Manager VETO: {round_.risk_response.get('rejection_reason', '')}"
            await self._publish_rejection(round_)
            return

        # 코사인 유사도 체크 (퀀트 방향 vs 애널리스트 방향)
        quant_direction = 1.0 if signal.get("direction") == "BUY" else -1.0
        analyst_direction = round_.analyst_response.get("market_direction_score", 0)
        similarity = self._cosine_similarity(quant_direction, analyst_direction)

        if similarity < self._similarity_min:
            round_.result = "rejected"
            round_.rejection_reason = (
                f"Direction mismatch: similarity={similarity:.2f} < {self._similarity_min}"
            )
            await self._publish_rejection(round_)
            return

        # 2-out-of-3 쿼럼 체크
        if round_.vote_count < self._quorum_required:
            round_.result = "rejected"
            round_.rejection_reason = (
                f"Quorum not met: {round_.vote_count}/{self._quorum_required}"
            )
            await self._publish_rejection(round_)
            return

        # ── 합의 달성 → LLM 최종 판단 ──
        final_decision = await self._llm_final_decision(round_, similarity)

        if final_decision.get("final_decision") == "approve":
            round_.result = "approved"
            await self._publish_approval(round_, similarity, final_decision)
        else:
            round_.result = "rejected"
            round_.rejection_reason = final_decision.get("reasoning", "LLM final rejection")
            await self._publish_rejection(round_)

    def _cosine_similarity(self, quant_direction: float, analyst_score: float) -> float:
        """방향성 유사도 계산.

        퀀트 방향(+1/-1)과 애널리스트 시장 방향 점수(-1.0~+1.0)의
        정규화된 유사도를 반환한다 (0.0~1.0).
        예: quant=+1, analyst=+0.35 → 0.675 (방향 일치, 강도 부분 일치)
        """
        if quant_direction == 0 or analyst_score == 0:
            return 0.0
        # 방향 일치 시: 0.5 + (analyst 강도 * 0.5)
        # 방향 불일치 시: 0.5 - (analyst 강도 * 0.5)
        sign_match = (quant_direction > 0) == (analyst_score > 0)
        strength = abs(analyst_score)
        if sign_match:
            return 0.5 + strength * 0.5
        return 0.5 - strength * 0.5

    async def _llm_final_decision(
        self, round_: ConsensusRound, similarity: float
    ) -> dict[str, Any]:
        """LLM 최종 의사결정."""
        signal = round_.signal

        system_prompt = (
            "You are a CTO synthesizing decisions from multiple specialist teams.\n"
            "Respond with valid JSON:\n"
            '{"final_decision": "approve"|"reject", "confidence": float(0-1), '
            '"reasoning": str, "position_size_adjustment": float(0.5-1.0)}'
        )

        user_prompt = (
            f"Signal: {signal.get('symbol')} {signal.get('direction')} "
            f"(score={signal.get('signal_score')})\n"
            f"Quant approval: YES\n"
            f"Analyst approval: {'YES' if round_.analyst_vote else 'NO'} "
            f"(direction={round_.analyst_response.get('market_direction_score', 0):.2f})\n"
            f"Risk approval: {'YES' if round_.risk_vote else 'NO'} "
            f"(level={round_.risk_response.get('risk_level', 'unknown')})\n"
            f"Cosine similarity: {similarity:.2f}\n"
            f"Consensus votes: {round_.vote_count}/{self._quorum_required}\n"
            f"Risk veto: {round_.risk_veto}\n\n"
            f"Proceed with trade? Final decision?"
        )

        response = await self._llm_chat([
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_prompt),
        ])

        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            return {"final_decision": "approve", "confidence": 0.5, "reasoning": response.content}

    # ── 결과 발행 ──

    async def _publish_approval(
        self, round_: ConsensusRound, similarity: float, decision: dict[str, Any]
    ) -> None:
        """합의 승인 발행."""
        signal = round_.signal
        payload = {
            "signal_id": round_.signal_id,
            "round_id": round_.round_id,
            "symbol": signal.get("symbol"),
            "direction": signal.get("direction"),
            "signal_score": signal.get("signal_score"),
            "entry_price": signal.get("entry_price"),
            "target_price": signal.get("target_price"),
            "stop_loss_price": signal.get("stop_loss_price"),
            "holding_period": signal.get("holding_period"),
            "consensus_votes": round_.vote_count,
            "cosine_similarity": similarity,
            "confidence": decision.get("confidence", 0),
            "position_size_adjustment": decision.get("position_size_adjustment", 1.0),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        await self._publish("orchestrator:approval", payload)
        await self._publish("orchestrator:consensus_approved", payload)
        logger.info("[%s] APPROVED: %s %s (votes=%d, sim=%.2f)",
                     self.name, signal.get("direction"), signal.get("symbol"),
                     round_.vote_count, similarity)

    async def _publish_rejection(self, round_: ConsensusRound) -> None:
        """합의 거부 발행."""
        await self._publish("orchestrator:rejection", {
            "signal_id": round_.signal_id,
            "round_id": round_.round_id,
            "symbol": round_.signal.get("symbol"),
            "rejection_reason": round_.rejection_reason,
            "consensus_votes": round_.vote_count,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        })
        logger.info("[%s] REJECTED: %s - %s",
                     self.name, round_.signal.get("symbol"), round_.rejection_reason)

    # ── 유틸리티 ──

    async def _cleanup_expired_rounds(self) -> None:
        """타임아웃된 합의 라운드 정리."""
        expired = [sid for sid, r in self._active_rounds.items() if r.is_expired()]
        for sid in expired:
            round_ = self._active_rounds.pop(sid)
            if not round_.resolved:
                round_.resolved = True
                round_.result = "rejected"
                round_.rejection_reason = "Consensus timeout"
                await self._publish_rejection(round_)
                logger.warning("[%s] Round timeout: %s", self.name, sid)
