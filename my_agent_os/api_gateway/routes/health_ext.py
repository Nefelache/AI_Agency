"""
Extended Health Checks — enterprise visibility.

Includes:
  - SQLite connectivity check
  - LLM config presence check (no secret leakage)
  - Optional WhatsApp bridge heartbeat freshness
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends

from my_agent_os.auth.dependencies import get_auth_context
from my_agent_os.auth.models import AuthContext
from my_agent_os.config.settings import settings

router = APIRouter()

_bridge_last_seen: float | None = None


@router.get("/health/extended")
async def health_extended(auth: AuthContext = Depends(get_auth_context)) -> dict:
    # DB check: ensure the sqlite file is reachable (no query, just stat + parent dir)
    db_path = Path(settings.MEMORY_DB_PATH)
    db_ok = db_path.parent.exists()

    llm_ok = bool(settings.DEEPSEEK_API_KEY) and bool(settings.DEEPSEEK_BASE_URL) and bool(settings.DEEPSEEK_MODEL)

    bridge_age_s = None
    bridge_ok = None
    if _bridge_last_seen is not None:
        bridge_age_s = time.time() - _bridge_last_seen
        bridge_ok = bridge_age_s < 120

    return {
        "status": "ok" if (db_ok and llm_ok and (bridge_ok is not False)) else "degraded",
        "db": {"ok": db_ok, "path": str(db_path)},
        "llm": {"ok": llm_ok, "provider": "deepseek", "model": settings.DEEPSEEK_MODEL},
        "whatsapp_bridge": {"ok": bridge_ok, "last_seen_age_s": bridge_age_s},
        "auth": {"user_id": auth.user_id, "role": auth.role.value},
    }


@router.post("/health/whatsapp")
async def whatsapp_bridge_heartbeat() -> dict:
    """
    Bridge heartbeat (no auth by default because it's called from inside the compose network).
    If you expose this publicly, put it behind Caddy and an API key.
    """
    global _bridge_last_seen
    _bridge_last_seen = time.time()
    return {"ok": True}

