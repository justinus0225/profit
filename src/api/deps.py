"""API 의존성 주입 - 전역 상태 접근 헬퍼.

main.py의 lifespan에서 초기화된 전역 객체를 API 라우터에 제공한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from src.core.boot_sequence import BootStatus
    from src.core.config import ProfitConfig
    from src.core.llm.router import LLMRouter


def get_config() -> ProfitConfig:
    """현재 설정 반환."""
    from src.main import _config
    if _config is None:
        raise RuntimeError("Config not initialized")
    return _config


def get_redis() -> aioredis.Redis | None:
    """Redis 클라이언트 반환."""
    from src.main import _redis
    return _redis


def get_llm_router() -> LLMRouter | None:
    """LLM 라우터 반환."""
    from src.main import _llm_router
    return _llm_router


def get_boot_status() -> BootStatus | None:
    """부트 상태 반환."""
    from src.main import _boot_status
    return _boot_status
