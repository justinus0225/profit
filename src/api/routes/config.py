"""설정 관리 API - 설정 조회/변경, 프리셋, 감사 로그.

ARCHITECTURE.md: Control Plane REST API.
CONFIG_REFERENCE.md: 설정 키 · 범위 · 리스크 레벨.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.api.schemas import (
    ApplyPresetRequest,
    ApplyPresetResponse,
    AuditLogEntry,
    AuditLogResponse,
    ConfigBatchUpdateRequest,
    ConfigBatchUpdateResponse,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
    ConfigValueResponse,
    PresetInfo,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])

# 설정 변경 감사 로그 (인메모리, 후속 DB 연동)
_audit_log: list[dict[str, Any]] = []


def _get_config_value(config: Any, key: str) -> Any:
    """점 표기법 키로 설정 값 조회. 예: 'fund.reserve_ratio'."""
    parts = key.split(".")
    obj = config
    for part in parts:
        if hasattr(obj, part):
            obj = getattr(obj, part)
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            raise KeyError(f"Config key not found: {key}")
    return obj


def _set_config_value(config: Any, key: str, value: Any) -> Any:
    """점 표기법 키로 설정 값 변경. 이전 값 반환."""
    parts = key.split(".")
    obj = config
    for part in parts[:-1]:
        if hasattr(obj, part):
            obj = getattr(obj, part)
        else:
            raise KeyError(f"Config key not found: {key}")

    field_name = parts[-1]
    if not hasattr(obj, field_name):
        raise KeyError(f"Config key not found: {key}")

    old_value = getattr(obj, field_name)
    setattr(obj, field_name, value)
    return old_value


@router.get("", response_model=dict[str, Any])
async def get_config(
    category: str | None = Query(None, description="설정 카테고리 필터"),
) -> dict[str, Any]:
    """전체 설정 조회."""
    from src.api.deps import get_config

    config = get_config()
    now = datetime.now(tz=timezone.utc).isoformat()

    if category:
        if not hasattr(config, category):
            raise HTTPException(status_code=404, detail=f"Category not found: {category}")
        section = getattr(config, category)
        return {
            "timestamp": now,
            "config": {category: section.model_dump() if hasattr(section, "model_dump") else section},
        }

    return {
        "timestamp": now,
        "config": config.model_dump(),
    }


@router.get("/{key}", response_model=ConfigValueResponse)
async def get_config_value(key: str) -> ConfigValueResponse:
    """단일 설정 값 조회."""
    from src.api.deps import get_config

    config = get_config()
    try:
        value = _get_config_value(config, key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")

    return ConfigValueResponse(key=key, value=value)


@router.post("/update", response_model=ConfigUpdateResponse)
async def update_config(req: ConfigUpdateRequest) -> ConfigUpdateResponse:
    """단일 설정 변경."""
    from src.api.deps import get_config, get_redis

    config = get_config()
    redis = get_redis()
    now = datetime.now(tz=timezone.utc).isoformat()

    try:
        old_value = _set_config_value(config, req.key, req.value)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Config key not found: {req.key}")
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid value: {e}")

    audit_id = f"AL-{datetime.now(tz=timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"
    _audit_log.append({
        "id": audit_id,
        "timestamp": now,
        "source": "api",
        "key": req.key,
        "old_value": old_value,
        "new_value": req.value,
        "reason": req.reason,
        "status": "applied",
    })

    logger.info("Config updated: %s = %s → %s (reason=%s)",
                req.key, old_value, req.value, req.reason)

    if redis:
        await redis.publish("system:config_changed", json.dumps({
            "key": req.key,
            "old_value": old_value,
            "new_value": req.value,
            "timestamp": now,
        }))

    return ConfigUpdateResponse(
        status="success",
        key=req.key,
        old_value=old_value,
        new_value=req.value,
        timestamp=now,
    )


@router.post("/batch-update", response_model=ConfigBatchUpdateResponse)
async def batch_update_config(req: ConfigBatchUpdateRequest) -> ConfigBatchUpdateResponse:
    """복수 설정 일괄 변경."""
    from src.api.deps import get_config

    config = get_config()
    now = datetime.now(tz=timezone.utc).isoformat()
    results: list[dict[str, Any]] = []

    for update in req.updates:
        key = update.get("key", "")
        value = update.get("value")
        try:
            old_value = _set_config_value(config, key, value)
            results.append({"key": key, "status": "success", "old": old_value, "new": value})

            audit_id = f"AL-{datetime.now(tz=timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"
            _audit_log.append({
                "id": audit_id,
                "timestamp": now,
                "source": "api",
                "key": key,
                "old_value": old_value,
                "new_value": value,
                "reason": req.reason,
                "status": "applied",
            })
        except (KeyError, TypeError, ValueError) as e:
            results.append({"key": key, "status": "failed", "error": str(e)})

    all_ok = all(r["status"] == "success" for r in results)
    return ConfigBatchUpdateResponse(
        status="success" if all_ok else "partial_failure",
        results=results,
        timestamp=now,
    )


@router.get("/presets/list", response_model=list[PresetInfo])
async def get_presets() -> list[PresetInfo]:
    """프리셋 목록 조회."""
    from src.api.deps import get_config

    config = get_config()
    presets: list[PresetInfo] = []

    for preset_id, changes in config.presets.items():
        presets.append(PresetInfo(
            id=preset_id,
            name=preset_id.replace("_", " ").title(),
            changes=changes,
        ))

    return presets


@router.post("/apply-preset", response_model=ApplyPresetResponse)
async def apply_preset(req: ApplyPresetRequest) -> ApplyPresetResponse:
    """프리셋 적용."""
    from src.api.deps import get_config

    config = get_config()
    now = datetime.now(tz=timezone.utc).isoformat()

    if req.preset_id not in config.presets:
        raise HTTPException(status_code=404, detail=f"Preset not found: {req.preset_id}")

    changes = config.presets[req.preset_id]
    applied = 0
    for key, value in changes.items():
        try:
            _set_config_value(config, key, value)
            applied += 1
        except (KeyError, TypeError, ValueError):
            logger.warning("Preset %s: failed to apply %s=%s", req.preset_id, key, value)

    logger.info("Preset applied: %s (%d changes)", req.preset_id, applied)

    return ApplyPresetResponse(
        status="success",
        preset_id=req.preset_id,
        changes_applied=applied,
        timestamp=now,
    )


@router.get("/audit-log/list", response_model=AuditLogResponse)
async def get_audit_log(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AuditLogResponse:
    """설정 변경 감사 로그 조회."""
    total = len(_audit_log)
    entries = _audit_log[offset:offset + limit]

    return AuditLogResponse(
        total=total,
        offset=offset,
        limit=limit,
        entries=[AuditLogEntry(**e) for e in reversed(entries)],
    )
