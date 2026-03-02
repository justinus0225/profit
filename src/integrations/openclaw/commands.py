"""OpenClaw 명령어 레지스트리.

관리자가 사용 가능한 명령어:
  /status  — 시스템 상태 요약
  /agents  — 에이전트 상태
  /pause   — 매매 일시 중단
  /resume  — 매매 재개
  /risk    — 현재 리스크 레벨
  /balance — 자산 잔고 요약
  /help    — 명령어 도움말
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_command(text: str) -> tuple[str, list[str]]:
    """텍스트에서 명령어와 인자를 분리한다."""
    text = text.strip()
    if text.startswith("/"):
        text = text[1:]
    parts = text.split()
    cmd = parts[0].lower() if parts else "help"
    args = parts[1:]
    return cmd, args


async def _cmd_status(redis: Any, _args: list[str]) -> str:
    """시스템 상태 요약."""
    info: dict[str, Any] = {}
    if redis:
        try:
            trading = await redis.get("system:trading_enabled")
            paper = await redis.get("system:paper_trading")
            info["trading_enabled"] = trading or "unknown"
            info["paper_trading"] = paper or "unknown"
        except Exception:
            pass
    return (
        f"**System Status**\n"
        f"- Trading: {info.get('trading_enabled', 'N/A')}\n"
        f"- Paper Trading: {info.get('paper_trading', 'N/A')}"
    )


async def _cmd_agents(redis: Any, _args: list[str]) -> str:
    """에이전트 상태 조회."""
    agents = [
        "analyst", "quant", "risk_manager", "portfolio_manager",
        "executor", "data_engineer", "qa", "orchestrator",
    ]
    lines = ["**Agent Status**"]
    for name in agents:
        status = "running"
        if redis:
            try:
                s = await redis.get(f"agent:{name}:status")
                if s:
                    status = s
            except Exception:
                pass
        lines.append(f"- {name}: {status}")
    return "\n".join(lines)


async def _cmd_pause(redis: Any, _args: list[str]) -> str:
    """매매 일시 중단."""
    if redis:
        await redis.set("system:trading_enabled", "false")
        await redis.publish("system:command", json.dumps({"action": "pause_trading"}))
    return "Trading PAUSED. Use /resume to restart."


async def _cmd_resume(redis: Any, _args: list[str]) -> str:
    """매매 재개."""
    if redis:
        await redis.set("system:trading_enabled", "true")
        await redis.publish("system:command", json.dumps({"action": "resume_trading"}))
    return "Trading RESUMED."


async def _cmd_risk(redis: Any, _args: list[str]) -> str:
    """현재 리스크 레벨."""
    level = "unknown"
    if redis:
        try:
            level = await redis.get("risk:current_level") or "low"
        except Exception:
            pass
    return f"**Current Risk Level**: {level}"


async def _cmd_balance(redis: Any, _args: list[str]) -> str:
    """자산 잔고 요약."""
    if redis:
        try:
            raw = await redis.get("portfolio:balance_summary")
            if raw:
                bal = json.loads(raw)
                return (
                    f"**Balance Summary**\n"
                    f"- Total: ${bal.get('total_usd', 0):,.2f}\n"
                    f"- Available: ${bal.get('available_usd', 0):,.2f}\n"
                    f"- In Positions: ${bal.get('in_positions_usd', 0):,.2f}"
                )
        except Exception:
            pass
    return "Balance information not available."


async def _cmd_help(_redis: Any, _args: list[str]) -> str:
    """명령어 도움말."""
    return (
        "**Available Commands**\n"
        "- /status  — System status summary\n"
        "- /agents  — Agent status overview\n"
        "- /pause   — Pause all trading\n"
        "- /resume  — Resume trading\n"
        "- /risk    — Current risk level\n"
        "- /balance — Balance summary\n"
        "- /help    — This help message"
    )


COMMAND_REGISTRY = {
    "status": _cmd_status,
    "agents": _cmd_agents,
    "pause": _cmd_pause,
    "resume": _cmd_resume,
    "risk": _cmd_risk,
    "balance": _cmd_balance,
    "help": _cmd_help,
}
