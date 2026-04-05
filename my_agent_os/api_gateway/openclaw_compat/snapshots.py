"""
Real-ish snapshots for OpenClaw Control UI (compat gateway).

Priority: channels (WhatsApp + bridge heartbeat), skills registry, sessions/main, health/models.
"""

from __future__ import annotations

import hashlib
import time
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from my_agent_os.config.channel_policies import get_whatsapp_config
from my_agent_os.config.settings import settings
from my_agent_os.skills_layer.tools import list_tools
from my_agent_os.version import __version__ as APP_VERSION


def _bridge_freshness() -> tuple[bool, str | None]:
    from my_agent_os.api_gateway.routes.health_ext import get_whatsapp_bridge_last_seen

    ts = get_whatsapp_bridge_last_seen()
    if ts is None:
        return False, "No Baileys heartbeat yet (is whatsapp-bridge running?)"
    age = time.time() - ts
    if age > 120:
        return False, f"Bridge silent for {int(age)}s (expect POST /health/whatsapp every ~30s)"
    return True, None


def _bridge_last_seen_ms() -> int | None:
    from my_agent_os.api_gateway.routes.health_ext import get_whatsapp_bridge_last_seen

    ts = get_whatsapp_bridge_last_seen()
    return int(ts * 1000) if ts is not None else None


def build_channels_status() -> dict[str, Any]:
    wa = get_whatsapp_config()
    enabled = bool(wa.get("enabled", True))
    ok, err = _bridge_freshness()
    ts_ms = int(time.time() * 1000)
    cloud_configured = bool(
        (settings.WHATSAPP_PHONE_ID or "").strip() and (settings.WHATSAPP_ACCESS_TOKEN or "").strip()
    )

    wa_status: dict[str, Any] = {
        "configured": enabled,
        "linked": ok and enabled,
        "running": ok and enabled,
        "connected": ok and enabled,
        "reconnectAttempts": 0,
        "lastConnectedAt": _bridge_last_seen_ms() if ok else None,
        "lastError": None if ok else err,
    }

    accounts: list[dict[str, Any]] = [
        {
            "accountId": "baileys",
            "name": "WhatsApp Web (Baileys)",
            "enabled": enabled,
            "configured": enabled,
            "linked": ok,
            "connected": ok,
            "running": ok,
            "lastError": wa_status["lastError"],
        }
    ]
    if cloud_configured:
        accounts.append(
            {
                "accountId": "cloud-api",
                "name": "WhatsApp Cloud API",
                "enabled": True,
                "configured": True,
                "linked": True,
                "connected": True,
                "running": True,
            }
        )

    return {
        "ts": ts_ms,
        "channelOrder": ["whatsapp"],
        "channelLabels": {"whatsapp": "WhatsApp"},
        "channels": {
            "whatsapp": wa_status,
        },
        "channelAccounts": {"whatsapp": accounts},
        "channelDefaultAccountId": {"whatsapp": "baileys"},
        "channelDetailLabels": {
            "whatsapp": f"dm_policy={wa.get('dm_policy', 'open')} · group_policy={wa.get('group_policy', 'allowlist')}"
        },
    }


def build_skills_status() -> dict[str, Any]:
    tools = list_tools()
    entries: list[dict[str, Any]] = []
    tools_dir = Path(__file__).resolve().parents[2] / "skills_layer" / "tools"
    base = str(tools_dir)
    for t in tools:
        name = str(t.get("name", ""))
        desc = str(t.get("description", ""))
        entries.append(
            {
                "name": name,
                "description": desc,
                "source": "coreclaw",
                "filePath": f"{base}/{name}.py",
                "baseDir": base,
                "skillKey": name,
                "bundled": False,
                "always": False,
                "disabled": False,
                "blockedByAllowlist": False,
                "eligible": True,
                "requirements": {"bins": [], "env": [], "config": [], "os": []},
                "missing": {"bins": [], "env": [], "config": [], "os": []},
            }
        )
    return {
        "workspaceDir": str(Path.cwd()),
        "managedSkillsDir": base,
        "skills": entries,
    }


def build_models_list() -> dict[str, Any]:
    models: list[dict[str, Any]] = []
    if (settings.DEEPSEEK_API_KEY or "").strip():
        models.append(
            {
                "id": settings.DEEPSEEK_MODEL,
                "name": settings.DEEPSEEK_MODEL,
                "provider": "deepseek",
                "contextWindow": 64000,
                "input": ["text"],
            }
        )
    return {"models": models}


def build_health_payload() -> dict[str, Any]:
    db_path = Path(settings.MEMORY_DB_PATH)
    llm_ok = bool(settings.DEEPSEEK_API_KEY) and bool(settings.DEEPSEEK_MODEL)
    bridge_ok, _ = _bridge_freshness()
    ts = int(time.time() * 1000)
    return {
        "ok": llm_ok,
        "ts": ts,
        "durationMs": 0,
        "heartbeatSeconds": 30,
        "defaultAgentId": "coreclaw",
        "agents": [{"id": "coreclaw", "name": "CoreClaw"}],
        "sessions": {
            "path": str(db_path.parent),
            "count": 0,
            "recent": [],
        },
        "service": "coreclaw",
        "version": APP_VERSION,
        "whatsapp_bridge_ok": bridge_ok,
        "memory_db": str(db_path),
        "memory_db_parent_exists": db_path.parent.exists(),
    }


def build_status_payload() -> dict[str, Any]:
    _, bridge_err = _bridge_freshness()
    return {
        "service": "coreclaw",
        "version": APP_VERSION,
        "gateway": "coreclaw-openclaw-compat",
        "whatsapp_bridge": {"ok": bridge_err is None, "detail": bridge_err},
        "llm": {
            "provider": "deepseek",
            "model": settings.DEEPSEEK_MODEL,
            "configured": bool((settings.DEEPSEEK_API_KEY or "").strip()),
        },
    }


def build_sessions_list(session_histories: dict[str, list[Any]]) -> dict[str, Any]:
    now = int(time.time() * 1000)
    rows: list[dict[str, Any]] = []
    keys = set(session_histories.keys())
    keys.add("main")
    for key in sorted(keys):
        hist = session_histories.get(key, [])
        rows.append(
            {
                "key": key,
                "kind": "global",
                "label": key,
                "displayName": f"Session {key}",
                "updatedAt": now if hist else None,
                "model": settings.DEEPSEEK_MODEL if settings.DEEPSEEK_API_KEY else None,
                "modelProvider": "deepseek" if settings.DEEPSEEK_API_KEY else None,
            }
        )
    if not rows:
        rows.append(
            {
                "key": "main",
                "kind": "global",
                "label": "main",
                "displayName": "Main",
                "updatedAt": None,
                "model": settings.DEEPSEEK_MODEL if settings.DEEPSEEK_API_KEY else None,
                "modelProvider": "deepseek" if settings.DEEPSEEK_API_KEY else None,
            }
        )
    return {
        "ts": now,
        "path": str(Path(settings.MEMORY_DB_PATH).parent),
        "count": len(rows),
        "defaults": {
            "modelProvider": "deepseek" if settings.DEEPSEEK_API_KEY else None,
            "model": settings.DEEPSEEK_MODEL if settings.DEEPSEEK_API_KEY else None,
            "contextTokens": None,
        },
        "sessions": rows,
    }


def channels_yaml_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "channels.yaml"


def _runtime_redacted() -> dict[str, Any]:
    return {
        "deepseek": {
            "base_url": settings.DEEPSEEK_BASE_URL,
            "model": settings.DEEPSEEK_MODEL,
            "api_key_configured": bool((settings.DEEPSEEK_API_KEY or "").strip()),
        },
        "whatsapp": {
            "bridge_secret_configured": bool((settings.WHATSAPP_BRIDGE_SECRET or "").strip()),
            "allow_from_env": bool((settings.WHATSAPP_ALLOW_FROM or "").strip()),
            "cloud_api_configured": bool(
                (settings.WHATSAPP_PHONE_ID or "").strip()
                and (settings.WHATSAPP_ACCESS_TOKEN or "").strip()
            ),
        },
        "openclaw_control_ui": {
            "token_configured": bool((settings.OPENCLAW_GATEWAY_TOKEN or "").strip()),
        },
        "memory_db_path": settings.MEMORY_DB_PATH,
        "audit_enabled": settings.AUDIT_ENABLED,
    }


def build_config_snapshot() -> dict[str, Any]:
    """channels.yaml raw + merged parsed view (secrets never emitted)."""
    path = channels_yaml_path()
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    issues: list[dict[str, str]] = []
    parsed_yaml: dict[str, Any] = {}
    if raw.strip():
        try:
            loaded = yaml.safe_load(raw)
            parsed_yaml = loaded if isinstance(loaded, dict) else {}
        except Exception as e:
            issues.append({"path": str(path), "message": f"YAML parse error: {e}"})
            parsed_yaml = {"_parse_error": str(e)}
    merged = {
        **parsed_yaml,
        "_coreclaw_runtime": _runtime_redacted(),
    }
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else "empty"
    return {
        "path": str(path),
        "exists": path.exists(),
        "raw": raw if raw else "# channels.yaml not found\n",
        "hash": h,
        "parsed": merged,
        "valid": len(issues) == 0,
        "config": merged,
        "issues": issues,
    }


def tail_gateway_logs(params: dict[str, Any]) -> dict[str, Any]:
    """Stream tail of latest audit_*.jsonl (OpenClaw logs.tail shape)."""
    from my_agent_os.enterprise.audit import audit_dir

    cur = params.get("cursor")
    if cur is not None and not isinstance(cur, (int, float)):
        try:
            cur = int(cur)
        except (TypeError, ValueError):
            cur = None
    if isinstance(cur, float):
        cur = int(cur)

    limit = int(params.get("limit", 200) or 200)
    limit = max(1, min(limit, 2_000))
    max_bytes = int(params.get("maxBytes", 65_536) or 65_536)
    max_bytes = max(4_096, min(max_bytes, 2 * 1024 * 1024))

    d = audit_dir()
    files = sorted(d.glob("audit_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {
            "file": None,
            "cursor": 0,
            "size": 0,
            "lines": [
                "[coreclaw] No audit JSONL yet — enable AUDIT_ENABLED and route traffic, "
                "or check my_agent_os/memory_layer/data/audit/"
            ],
            "truncated": False,
            "reset": True,
        }

    path = files[0]
    data = path.read_bytes()
    size = len(data)

    if cur is None or cur < 0 or cur > size:
        chunk_start = max(0, size - max_bytes)
        chunk = data[chunk_start:]
        if chunk_start > 0 and b"\n" in chunk:
            chunk = chunk.split(b"\n", 1)[1]
        text = chunk.decode("utf-8", errors="replace")
        lines = text.splitlines()
        truncated_top = chunk_start > 0
        if len(lines) > limit:
            lines = lines[-limit:]
            truncated = True
        else:
            truncated = truncated_top
        return {
            "file": str(path),
            "cursor": size,
            "size": size,
            "lines": lines,
            "truncated": truncated,
            "reset": True,
        }

    start = int(cur)
    chunk = data[start : start + max_bytes]
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) > limit:
        lines = lines[:limit]
    new_cursor = min(start + len(chunk), size)
    return {
        "file": str(path),
        "cursor": new_cursor,
        "size": size,
        "lines": lines,
        "truncated": new_cursor < size,
        "reset": False,
    }


def _zero_cost_totals() -> dict[str, Any]:
    return {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "totalTokens": 0,
        "totalCost": 0,
        "inputCost": 0,
        "outputCost": 0,
        "cacheReadCost": 0,
        "cacheWriteCost": 0,
        "missingCostEntries": 0,
    }


def _empty_sessions_usage_aggregates() -> dict[str, Any]:
    return {
        "messages": {
            "total": 0,
            "user": 0,
            "assistant": 0,
            "toolCalls": 0,
            "toolResults": 0,
            "errors": 0,
        },
        "tools": {"totalCalls": 0, "uniqueTools": 0, "tools": []},
        "byModel": [],
        "byProvider": [],
        "byAgent": [],
        "byChannel": [],
        "daily": [],
    }


def build_sessions_usage(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """OpenClaw `sessions.usage` result shape (Control UI Overview / Usage tabs)."""
    p = params or {}
    today = date.today().isoformat()
    start = str(p.get("startDate") or today)
    end = str(p.get("endDate") or today)
    return {
        "updatedAt": int(time.time() * 1000),
        "startDate": start,
        "endDate": end,
        "sessions": [],
        "totals": _zero_cost_totals(),
        "aggregates": _empty_sessions_usage_aggregates(),
    }


def build_sessions_usage_timeseries(session_key: str) -> dict[str, Any]:
    out: dict[str, Any] = {"points": []}
    if session_key:
        out["sessionId"] = session_key
    return out


def build_sessions_usage_logs() -> dict[str, Any]:
    return {"logs": []}


def build_usage_status() -> dict[str, Any]:
    return {
        **build_sessions_usage(),
        "_note": "CoreClaw compat gateway does not aggregate Pi-style usage yet.",
    }


def build_usage_cost() -> dict[str, Any]:
    return {
        "updatedAt": int(time.time() * 1000),
        "days": 0,
        "daily": [],
        "totals": _zero_cost_totals(),
        "_note": "CoreClaw compat: cost tracking not wired.",
    }


def build_cron_status() -> dict[str, Any]:
    return {"enabled": False, "jobs": 0, "nextWakeAtMs": None}


def build_system_presence() -> list[dict[str, Any]]:
    bridge_ok, err = _bridge_freshness()
    return [
        {
            "instanceId": "coreclaw-gateway",
            "host": "Neural Gateway",
            "version": APP_VERSION,
            "platform": "python",
            "mode": "gateway",
            "roles": ["gateway"],
            "reason": "coreclaw",
        },
        {
            "instanceId": "whatsapp-baileys",
            "host": "WhatsApp bridge",
            "version": "",
            "platform": "node",
            "mode": "channel",
            "roles": ["whatsapp"],
            "reason": "heartbeat_ok" if bridge_ok else (err or "unknown"),
        },
    ]
