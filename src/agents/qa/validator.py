"""QA 검증 모듈.

에이전트 출력 품질 검증, 신호 일관성 체크,
시스템 상태 정상성 검증을 수행한다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class QAValidator:
    """에이전트 출력 및 시스템 상태 검증."""

    def __init__(self) -> None:
        self._validation_results: list[dict[str, Any]] = []

    def validate_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        """매매 신호의 필수 필드 및 값 범위를 검증한다.

        Returns:
            {"valid": bool, "errors": list[str]} dict.
        """
        errors: list[str] = []
        required_fields = [
            "signal_id", "symbol", "direction", "signal_score",
            "entry_price", "target_price", "stop_loss_price",
        ]

        for field in required_fields:
            if field not in signal or signal[field] is None:
                errors.append(f"Missing required field: {field}")

        # 방향성 검증
        direction = signal.get("direction")
        if direction not in ("BUY", "SELL", None):
            errors.append(f"Invalid direction: {direction}")

        # 점수 범위 검증
        score = signal.get("signal_score")
        if score is not None and not (-100 <= score <= 100):
            errors.append(f"Signal score out of range: {score}")

        # 가격 일관성 검증
        entry = signal.get("entry_price", 0) or 0
        target = signal.get("target_price", 0) or 0
        stop_loss = signal.get("stop_loss_price", 0) or 0

        if entry > 0 and target > 0 and stop_loss > 0:
            if direction == "BUY":
                if target <= entry:
                    errors.append("BUY: target should be > entry")
                if stop_loss >= entry:
                    errors.append("BUY: stop_loss should be < entry")
            elif direction == "SELL":
                if target >= entry:
                    errors.append("SELL: target should be < entry")
                if stop_loss <= entry:
                    errors.append("SELL: stop_loss should be > entry")

        result = {"valid": len(errors) == 0, "errors": errors}
        self._validation_results.append(result)
        return result

    def validate_consensus(self, consensus_data: dict[str, Any]) -> dict[str, Any]:
        """합의 결과의 정합성을 검증한다."""
        errors: list[str] = []

        votes = consensus_data.get("consensus_votes", 0)
        if votes < 2:
            errors.append(f"Insufficient votes: {votes}")

        confidence = consensus_data.get("confidence", 0)
        if confidence < 0 or confidence > 1:
            errors.append(f"Confidence out of range: {confidence}")

        return {"valid": len(errors) == 0, "errors": errors}

    def get_stats(self) -> dict[str, Any]:
        """검증 통계."""
        total = len(self._validation_results)
        valid = sum(1 for r in self._validation_results if r["valid"])
        return {
            "total_validations": total,
            "valid_count": valid,
            "invalid_count": total - valid,
            "pass_rate": valid / total if total > 0 else 0.0,
        }
