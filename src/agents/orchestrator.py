"""오케스트레이터 에이전트 - 합의 조율, 최종 의사결정.

ARCHITECTURE.md: Level 3, Orchestrator / CTO
- 2-out-of-3 Quorum 합의 프로토콜 실행
- Risk Manager 거부권 처리
- 신호 → 검증 → 합의 → 실행 워크플로우 조율
- 코사인 유사도 기반 방향성 일치 검증
- ConsensusManager를 통한 합의 라운드 관리 + 메트릭
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.core.consensus import ConsensusManager, ConsensusResult, ConsensusRound
from src.core.llm.client import Message, Role

logger = logging.getLogger(__name__)


class OrchestratorAgent(BaseAgent):
    """오케스트레이터: 합의 프로토콜 + 워크플로우 조율.

    ConsensusManager에 합의 라운드 관리를 위임한다.
    """

    @property
    def agent_type(self) -> str:
        return "orchestrator"

    async def _on_initialize(self) -> None:
        self._signal_cfg = self._config.signal

        # ConsensusManager 초기화
        self._consensus = ConsensusManager(
            quorum_required=self._signal_cfg.consensus_quorum,
            similarity_min=self._signal_cfg.consensus_similarity_min,
        )

        # 이벤트 구독
        await self._subscribe("quant:signal", self._on_quant_signal)
        await self._subscribe("analyst:approval_response", self._on_analyst_response)
        await self._subscribe("risk:approval_response", self._on_risk_response)

    async def _on_run(self) -> None:
        """타임아웃 라운드 정리 루프."""
        while self._running:
            expired = self._consensus.cleanup_expired()
            for round_ in expired:
                await self._publish_rejection(round_)
            await asyncio.sleep(10)

    # ── 합의 프로토콜 ──

    async def _on_quant_signal(self, data: dict[str, Any]) -> None:
        """Step 1: 퀀트 신호 수신 → 합의 라운드 시작."""
        signal_id = data.get("signal_id", "")
        if not signal_id:
            return

        round_ = self._consensus.create_round(data)

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
        round_ = self._consensus.register_analyst_vote(
            signal_id, data.get("approval", False), data,
        )
        if not round_:
            return

        if round_.votes_collected:
            await self._resolve_consensus(round_)

    async def _on_risk_response(self, data: dict[str, Any]) -> None:
        """리스크 매니저 검증 응답 수신."""
        signal_id = data.get("signal_id", "")
        round_ = self._consensus.register_risk_vote(
            signal_id, data.get("approval", False), data,
        )
        if not round_:
            return

        if round_.votes_collected:
            await self._resolve_consensus(round_)

    async def _resolve_consensus(self, round_: ConsensusRound) -> None:
        """Step 3-4: 합의 결과 판정."""
        result = self._consensus.evaluate(round_)

        if result == ConsensusResult.REJECTED:
            await self._publish_rejection(round_)
            return

        if result == ConsensusResult.PENDING:
            return

        # ── 합의 달성 → LLM 최종 판단 ──
        final_decision = await self._llm_final_decision(round_)

        if final_decision.get("final_decision") == "approve":
            self._consensus.finalize_approval(round_, final_decision)
            await self._publish_approval(round_, final_decision)
        else:
            reason = final_decision.get("reasoning", "LLM final rejection")
            self._consensus.finalize_rejection(round_, reason)
            await self._publish_rejection(round_)

    async def _llm_final_decision(
        self, round_: ConsensusRound
    ) -> dict[str, Any]:
        """LLM 최종 의사결정."""
        signal = round_.signal

        system_prompt = (
            "You are a CTO synthesizing decisions from multiple specialist teams.\n"
            "Respond with valid JSON:\n"
            '{"final_decision": "approve"|"reject", "confidence": float(0-1), '
            '"reasoning": str, "position_size_adjustment": float(0.5-1.0)}'
        )

        analyst_vote = round_.analyst_vote
        risk_vote = round_.risk_vote

        analyst_direction = (
            analyst_vote.data.get("market_direction_score", 0) if analyst_vote else 0
        )
        risk_level = (
            risk_vote.data.get("risk_level", "unknown") if risk_vote else "unknown"
        )

        user_prompt = (
            f"Signal: {signal.get('symbol')} {signal.get('direction')} "
            f"(score={signal.get('signal_score')})\n"
            f"Quant approval: YES\n"
            f"Analyst approval: {'YES' if analyst_vote and analyst_vote.approved else 'NO'} "
            f"(direction={analyst_direction:.2f})\n"
            f"Risk approval: {'YES' if risk_vote and risk_vote.approved else 'NO'} "
            f"(level={risk_level})\n"
            f"Cosine similarity: {round_.cosine_similarity:.2f}\n"
            f"Consensus votes: {round_.vote_count}/{self._signal_cfg.consensus_quorum}\n"
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
        self, round_: ConsensusRound, decision: dict[str, Any]
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
            "cosine_similarity": round_.cosine_similarity,
            "confidence": decision.get("confidence", 0),
            "position_size_adjustment": decision.get("position_size_adjustment", 1.0),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        await self._publish("orchestrator:approval", payload)
        await self._publish("orchestrator:consensus_approved", payload)

        logger.info("[%s] APPROVED: %s %s (votes=%d, sim=%.2f)",
                     self.name, signal.get("direction"), signal.get("symbol"),
                     round_.vote_count, round_.cosine_similarity)

    async def _publish_rejection(self, round_: ConsensusRound) -> None:
        """합의 거부 발행."""
        await self._publish("orchestrator:rejection", {
            "signal_id": round_.signal_id,
            "round_id": round_.round_id,
            "symbol": round_.signal.get("symbol"),
            "rejection_reason": round_.rejection_detail,
            "consensus_votes": round_.vote_count,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        })
        logger.info("[%s] REJECTED: %s - %s",
                     self.name, round_.signal.get("symbol"), round_.rejection_detail)

    # ── 메트릭 ──

    @property
    def consensus_metrics(self) -> dict[str, Any]:
        """합의 메트릭 조회."""
        return self._consensus.metrics.to_dict()
