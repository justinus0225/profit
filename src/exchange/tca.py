"""거래 비용 분석 (TCA) 모듈 (ARCHITECTURE.md P5).

3단계 TCA:
1. 사전 분석 (Pre-Trade): 호가창 깊이, 스프레드, 예상 슬리피지
2. 실시간 분석 (In-Trade): 체결 중 슬리피지 모니터링
3. 사후 분석 (Post-Trade): Implementation Shortfall 계산

실행 품질 지표:
- Slippage: (체결가 - 의사결정가) / 의사결정가
- Implementation Shortfall: 의사결정 시점 대비 실제 비용
- Market Impact: 주문으로 인한 가격 변동
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REDIS_KEY_PREFIX = "tca"


@dataclass
class PreTradeAnalysis:
    """사전 분석 결과."""

    symbol: str
    decision_price: float  # 의사결정 시점 가격
    spread_pct: float  # 스프레드 (%)
    estimated_slippage_pct: float  # 예상 슬리피지 (%)
    recommended_order_type: str  # "market" | "limit" | "twap"
    timestamp: float = field(default_factory=time.time)


@dataclass
class PostTradeAnalysis:
    """사후 분석 결과."""

    symbol: str
    side: str
    decision_price: float  # 의사결정 시점 가격
    fill_price: float  # 실제 체결 가격
    slippage_pct: float  # 슬리피지 (%)
    slippage_usd: float  # 슬리피지 ($)
    implementation_shortfall_pct: float  # IS (%)
    fee_usd: float  # 수수료 ($)
    total_cost_usd: float  # 총 거래 비용 ($)
    quantity: float
    total_usd: float
    execution_time_ms: float  # 실행 소요 시간 (ms)
    order_type: str
    timestamp: float = field(default_factory=time.time)


class TCAModule:
    """거래 비용 분석 (Transaction Cost Analysis) 모듈."""

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client
        # 최근 분석 결과 캐시
        self._recent_analyses: list[PostTradeAnalysis] = []

    def pre_trade_analyze(
        self,
        symbol: str,
        side: str,
        quantity_usd: float,
        current_price: float,
        *,
        bid: float | None = None,
        ask: float | None = None,
        volume_24h: float | None = None,
    ) -> PreTradeAnalysis:
        """사전 분석: 최적 주문 방식을 결정한다.

        Args:
            symbol: 코인 심볼
            side: "buy" | "sell"
            quantity_usd: 주문 금액 ($)
            current_price: 현재 가격
            bid: 매수호가 (선택)
            ask: 매도호가 (선택)
            volume_24h: 24시간 거래량 ($, 선택)
        """
        # 스프레드 계산
        if bid and ask and ask > 0:
            spread_pct = (ask - bid) / ask * 100
        else:
            spread_pct = 0.05  # 기본 스프레드 추정

        # 예상 슬리피지 (Volume-based 모델)
        if volume_24h and volume_24h > 0:
            # 주문 크기 / 일 거래량 비율
            volume_ratio = quantity_usd / volume_24h
            # 볼륨 비율 기반 슬리피지 추정 (경험적 공식)
            estimated_slippage = volume_ratio * 100 * 0.5  # 50% 계수
            estimated_slippage = min(estimated_slippage, 2.0)  # 최대 2%
        else:
            estimated_slippage = spread_pct / 2

        # 주문 방식 추천
        if quantity_usd > 50000 or estimated_slippage > 0.3:
            order_type = "twap"
        elif estimated_slippage > 0.1:
            order_type = "limit"
        else:
            order_type = "market"

        return PreTradeAnalysis(
            symbol=symbol,
            decision_price=current_price,
            spread_pct=spread_pct,
            estimated_slippage_pct=estimated_slippage,
            recommended_order_type=order_type,
        )

    def post_trade_analyze(
        self,
        symbol: str,
        side: str,
        decision_price: float,
        fill_price: float,
        quantity: float,
        total_usd: float,
        fee_usd: float,
        order_type: str,
        execution_time_ms: float,
    ) -> PostTradeAnalysis:
        """사후 분석: 실행 품질을 평가한다.

        Args:
            symbol: 코인 심볼
            side: "buy" | "sell"
            decision_price: 의사결정 시점 가격
            fill_price: 실제 체결 가격
            quantity: 체결 수량
            total_usd: 체결 금액 ($)
            fee_usd: 수수료 ($)
            order_type: "market" | "limit" | "twap"
            execution_time_ms: 실행 소요 시간 (ms)
        """
        # 슬리피지 계산
        if side == "buy":
            slippage_pct = (fill_price - decision_price) / decision_price * 100
        else:
            slippage_pct = (decision_price - fill_price) / decision_price * 100
        slippage_usd = abs(slippage_pct / 100) * total_usd

        # Implementation Shortfall
        is_pct = slippage_pct + (fee_usd / total_usd * 100 if total_usd > 0 else 0)

        total_cost = slippage_usd + fee_usd

        result = PostTradeAnalysis(
            symbol=symbol,
            side=side,
            decision_price=decision_price,
            fill_price=fill_price,
            slippage_pct=round(slippage_pct, 4),
            slippage_usd=round(slippage_usd, 2),
            implementation_shortfall_pct=round(is_pct, 4),
            fee_usd=round(fee_usd, 2),
            total_cost_usd=round(total_cost, 2),
            quantity=quantity,
            total_usd=total_usd,
            execution_time_ms=execution_time_ms,
            order_type=order_type,
        )

        self._recent_analyses.append(result)
        # 최근 500건만 유지
        if len(self._recent_analyses) > 500:
            self._recent_analyses = self._recent_analyses[-500:]

        logger.info(
            "TCA: %s %s fill=%.2f decision=%.2f slip=%.4f%% IS=%.4f%% cost=$%.2f",
            side,
            symbol,
            fill_price,
            decision_price,
            slippage_pct,
            is_pct,
            total_cost,
        )

        return result

    async def save_analysis(self, analysis: PostTradeAnalysis) -> None:
        """TCA 결과를 Redis에 저장한다."""
        data = {
            "symbol": analysis.symbol,
            "side": analysis.side,
            "decision_price": analysis.decision_price,
            "fill_price": analysis.fill_price,
            "slippage_pct": analysis.slippage_pct,
            "slippage_usd": analysis.slippage_usd,
            "implementation_shortfall_pct": analysis.implementation_shortfall_pct,
            "fee_usd": analysis.fee_usd,
            "total_cost_usd": analysis.total_cost_usd,
            "quantity": analysis.quantity,
            "total_usd": analysis.total_usd,
            "execution_time_ms": analysis.execution_time_ms,
            "order_type": analysis.order_type,
            "timestamp": analysis.timestamp,
        }
        key = f"{REDIS_KEY_PREFIX}:history"
        await self._redis.lpush(key, json.dumps(data))
        await self._redis.ltrim(key, 0, 999)  # 최근 1000건 유지

    def get_summary(self, last_n: int = 100) -> dict[str, Any]:
        """최근 N건의 TCA 요약 통계를 반환한다."""
        recent = self._recent_analyses[-last_n:]
        if not recent:
            return {
                "count": 0,
                "avg_slippage_pct": 0.0,
                "avg_implementation_shortfall_pct": 0.0,
                "total_cost_usd": 0.0,
            }

        slippages = [a.slippage_pct for a in recent]
        is_values = [a.implementation_shortfall_pct for a in recent]
        total_cost = sum(a.total_cost_usd for a in recent)

        return {
            "count": len(recent),
            "avg_slippage_pct": round(sum(slippages) / len(slippages), 4),
            "max_slippage_pct": round(max(slippages), 4),
            "avg_implementation_shortfall_pct": round(
                sum(is_values) / len(is_values), 4
            ),
            "total_cost_usd": round(total_cost, 2),
            "by_order_type": self._group_by_order_type(recent),
        }

    def _group_by_order_type(
        self, analyses: list[PostTradeAnalysis]
    ) -> dict[str, Any]:
        """주문 유형별 통계."""
        groups: dict[str, list[PostTradeAnalysis]] = {}
        for a in analyses:
            groups.setdefault(a.order_type, []).append(a)

        result = {}
        for ot, items in groups.items():
            slippages = [a.slippage_pct for a in items]
            result[ot] = {
                "count": len(items),
                "avg_slippage_pct": round(sum(slippages) / len(slippages), 4),
            }
        return result
