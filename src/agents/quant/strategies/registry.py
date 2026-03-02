"""전략 레지스트리 — 전략 등록, 조회, 생명주기 상태 관리.

모든 전략(빌트인 + 생성된)이 StrategyEntry로 래핑되어
메타데이터와 생명주기 상태를 함께 관리한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# 전략 함수 시그니처: async def strategy(event) -> None
StrategyFn = Callable[..., Coroutine[Any, Any, None]]


class StrategyStatus(str, Enum):
    """전략 생명주기 상태."""

    CANDIDATE = "candidate"    # 신규 생성, 검증 전
    SHADOW = "shadow"          # 그림자 테스트 중
    LIVE = "live"              # 실제 신호 생성 활성화
    DEPRECATED = "deprecated"  # 비활성화 (성과 부진)


@dataclass
class StrategyEntry:
    """전략 등록 엔트리."""

    name: str
    strategy_fn: StrategyFn | None = None
    status: StrategyStatus = StrategyStatus.CANDIDATE
    parameters: dict[str, Any] = field(default_factory=dict)
    source: str = "builtin"  # "builtin" | "generated" | "optimized"
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    metrics: dict[str, float] = field(default_factory=dict)
    wfo_results: dict[str, Any] = field(default_factory=dict)
    shadow_start: datetime | None = None
    shadow_days_passed: int = 0


# 유효한 상태 전이 규칙
_VALID_TRANSITIONS: dict[StrategyStatus, set[StrategyStatus]] = {
    StrategyStatus.CANDIDATE: {StrategyStatus.SHADOW, StrategyStatus.DEPRECATED},
    StrategyStatus.SHADOW: {StrategyStatus.LIVE, StrategyStatus.DEPRECATED},
    StrategyStatus.LIVE: {StrategyStatus.DEPRECATED},
    StrategyStatus.DEPRECATED: {StrategyStatus.CANDIDATE},  # 재활성화 가능
}


class StrategyRegistry:
    """전략 레지스트리.

    asyncio 단일 스레드에서 사용을 가정한다.
    """

    def __init__(self, max_strategies: int = 50) -> None:
        self._strategies: dict[str, StrategyEntry] = {}
        self._max = max_strategies

    def register(self, entry: StrategyEntry) -> bool:
        """전략 등록. 최대 수 초과 시 False 반환."""
        if len(self._strategies) >= self._max and entry.name not in self._strategies:
            logger.warning(
                "Registry full (%d). Cannot register %s", self._max, entry.name
            )
            return False
        self._strategies[entry.name] = entry
        logger.info(
            "Strategy registered: %s (status=%s, source=%s)",
            entry.name, entry.status.value, entry.source,
        )
        return True

    def get(self, name: str) -> StrategyEntry | None:
        """이름으로 전략 조회."""
        return self._strategies.get(name)

    def get_by_status(self, status: StrategyStatus) -> list[StrategyEntry]:
        """상태별 전략 목록 반환."""
        return [e for e in self._strategies.values() if e.status == status]

    def transition(self, name: str, new_status: StrategyStatus) -> bool:
        """전략 상태 전이. 유효하지 않은 전이 시 False 반환."""
        entry = self._strategies.get(name)
        if not entry:
            logger.warning("Strategy not found: %s", name)
            return False

        valid = _VALID_TRANSITIONS.get(entry.status, set())
        if new_status not in valid:
            logger.warning(
                "Invalid transition: %s %s -> %s (valid: %s)",
                name, entry.status.value, new_status.value,
                [s.value for s in valid],
            )
            return False

        old = entry.status
        entry.status = new_status
        if new_status == StrategyStatus.SHADOW:
            entry.shadow_start = datetime.now(tz=timezone.utc)
            entry.shadow_days_passed = 0
        logger.info("Strategy %s: %s -> %s", name, old.value, new_status.value)
        return True

    def update_params(self, name: str, params: dict[str, Any]) -> bool:
        """전략 파라미터 업데이트."""
        entry = self._strategies.get(name)
        if not entry:
            return False
        entry.parameters.update(params)
        return True

    def update_metrics(self, name: str, metrics: dict[str, float]) -> bool:
        """전략 성과 지표 업데이트."""
        entry = self._strategies.get(name)
        if not entry:
            return False
        entry.metrics.update(metrics)
        return True

    def remove(self, name: str) -> bool:
        """전략 제거."""
        removed = self._strategies.pop(name, None)
        if removed:
            logger.info("Strategy removed: %s", name)
        return removed is not None

    def list_all(self) -> list[StrategyEntry]:
        """모든 전략 목록 반환."""
        return list(self._strategies.values())

    @property
    def count(self) -> int:
        return len(self._strategies)

    def to_summary(self) -> list[dict[str, Any]]:
        """직렬화 가능한 요약 목록 반환."""
        return [
            {
                "name": e.name,
                "status": e.status.value,
                "source": e.source,
                "parameters": e.parameters,
                "metrics": e.metrics,
                "created_at": e.created_at.isoformat(),
            }
            for e in self._strategies.values()
        ]
