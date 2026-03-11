"""
Audit Logging — Enterprise-grade traceability.

OpenClaw-style: full logging of prompts, tool calls, API calls, session context.
ISO 8601 timestamps, searchable, exportable.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from my_agent_os.config.settings import settings

logger = logging.getLogger(__name__)

_AUDIT_DIR = Path(__file__).parent.parent / "memory_layer" / "data" / "audit"


def _ensure_dir() -> Path:
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    return _AUDIT_DIR


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def log_route(
    *,
    session_id: str,
    channel: str,
    user_id: str,
    raw_input: str,
    response: dict[str, Any] | None = None,
    error: str | None = None,
    latency_ms: float | None = None,
) -> None:
    """Log a routing event (inbound → agent → response)."""
    if not settings.AUDIT_ENABLED:
        return
    try:
        entry = {
            "ts": _iso_now(),
            "event": "route",
            "session_id": session_id,
            "channel": channel,
            "user_id": user_id,
            "raw_input": raw_input[:2000],  # Truncate for storage
            "response_keys": list(response.keys()) if response else None,
            "error": error,
            "latency_ms": latency_ms,
        }
        _append_log(entry)
    except Exception as e:
        logger.warning("Audit log write failed: %s", e)


def log_tool_call(
    *,
    session_id: str,
    tool_name: str,
    args: dict[str, Any],
    result: Any = None,
    error: str | None = None,
) -> None:
    """Log a tool/skill invocation."""
    if not settings.AUDIT_ENABLED:
        return
    try:
        entry = {
            "ts": _iso_now(),
            "event": "tool_call",
            "session_id": session_id,
            "tool": tool_name,
            "args": args,
            "result_preview": str(result)[:500] if result else None,
            "error": error,
        }
        _append_log(entry)
    except Exception as e:
        logger.warning("Audit log write failed: %s", e)


def _append_log(entry: dict) -> None:
    """Append JSONL entry to daily log file."""
    dir_path = _ensure_dir()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = dir_path / f"audit_{today}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def prune_retention() -> dict[str, Any]:
    """
    Delete audit files older than settings.AUDIT_RETENTION_DAYS (by mtime).
    Safe to call on startup.
    """
    if not settings.AUDIT_ENABLED:
        return {"enabled": False, "deleted": 0}

    days = max(1, int(getattr(settings, "AUDIT_RETENTION_DAYS", 90) or 90))
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    deleted = 0

    dir_path = _ensure_dir()
    for p in dir_path.glob("audit_*.jsonl"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                deleted += 1
        except Exception as e:
            logger.warning("Audit retention prune failed for %s: %s", p, e)

    return {"enabled": True, "retention_days": days, "deleted": deleted}


def audit_dir() -> Path:
    return _ensure_dir()
