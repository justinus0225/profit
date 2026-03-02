"""포지션 사이징 모듈.

리스크 레벨, 포트폴리오 배분, 최대 단일 포지션 등을 고려하여
적정 포지션 크기를 계산한다.
"""

from __future__ import annotations

from typing import Any


class PositionSizer:
    """포지션 크기 계산."""

    def __init__(self, portfolio_config: Any) -> None:
        self._cfg = portfolio_config

    def calculate_position_size(
        self,
        total_balance: float,
        risk_level: str,
        max_single_position: float,
        reserve_ratio: float,
    ) -> float:
        """적정 포지션 크기 계산.

        Args:
            total_balance: 전체 자산.
            risk_level: 현재 리스크 레벨.
            max_single_position: 최대 단일 포지션 비율.
            reserve_ratio: 최소 보유금 비율.

        Returns:
            USD 기준 포지션 크기.
        """
        available = total_balance * (1 - reserve_ratio)
        max_size = available * max_single_position

        # 리스크 레벨에 따른 조정
        adjustments = {"low": 1.0, "medium": 0.7, "high": 0.4, "critical": 0.0}
        return max_size * adjustments.get(risk_level, 0.5)

    def check_concentration(
        self,
        positions: list[dict[str, Any]],
        new_symbol: str,
        max_concurrent_coins: int,
    ) -> bool:
        """포지션 집중도 체크.

        Returns:
            True → 신규 포지션 진입 가능.
        """
        current_symbols = {p.get("symbol") for p in positions}
        return (
            len(current_symbols) < max_concurrent_coins
            or new_symbol in current_symbols
        )
