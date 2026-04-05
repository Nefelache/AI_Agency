"""
Admin Routes — owner-only operational utilities.

GET /admin/openclaw-token  — retrieve or generate the WebSocket gateway token
GET /admin/config          — current non-secret runtime config summary
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from my_agent_os.auth.dependencies import require_role
from my_agent_os.auth.models import AuthContext, Role
from my_agent_os.api_gateway.openclaw_compat.gateway_ws import openclaw_ws_valid_tokens
from my_agent_os.config.settings import settings

router = APIRouter(prefix="/admin", tags=["Admin"])

_OWNER_DEP = require_role(Role.OWNER)


@router.get("/openclaw-token")
async def get_openclaw_token(
    auth: AuthContext = Depends(_OWNER_DEP),
) -> dict[str, Any]:
    """
    Return the value to paste into OpenClaw Control UI (Settings → Gateway → Token).
    Matches HTTP: use API_KEY_OWNER unless OPENCLAW_GATEWAY_TOKEN is set (optional extra).
    """
    dedicated = (settings.OPENCLAW_GATEWAY_TOKEN or "").strip()
    owner = (settings.API_KEY_OWNER or "").strip()

    if dedicated:
        return {
            "token": dedicated,
            "token_source": "OPENCLAW_GATEWAY_TOKEN",
            "ws_url": "wss://<your-domain>/openclaw",
            "status": "configured",
            "instructions": (
                "Paste in Control UI Gateway Token. HTTP still accepts API_KEY_*; "
                "WS also accepts the same keys, or this dedicated token."
            ),
        }
    if owner:
        return {
            "token": owner,
            "token_source": "API_KEY_OWNER",
            "ws_url": "wss://<your-domain>/openclaw",
            "status": "configured",
            "instructions": (
                "Use the same string as the web UI API key (Settings → Gateway → Token on /openclaw/). "
                "Optional: set OPENCLAW_GATEWAY_TOKEN for a separate WS-only secret."
            ),
        }

    suggested = secrets.token_urlsafe(32)
    return {
        "token": None,
        "status": "not_configured",
        "suggested_token": suggested,
        "instructions": (
            "Set API_KEY_OWNER in my_agent_os/config/.env to one secret for both the / UI and /openclaw WS, "
            f"then restart. Example:\n\n  API_KEY_OWNER={suggested}\n\n"
            "Alternatively set OPENCLAW_GATEWAY_TOKEN for WS only (HTTP still needs API keys)."
        ),
    }


@router.get("/config")
async def get_runtime_config(
    auth: AuthContext = Depends(_OWNER_DEP),
) -> dict[str, Any]:
    """Non-secret runtime config snapshot for debugging."""
    return {
        "deepseek_model": settings.DEEPSEEK_MODEL,
        "deepseek_configured": bool(settings.DEEPSEEK_API_KEY),
        "memory_db_path": settings.MEMORY_DB_PATH,
        "memory_top_k": settings.MEMORY_RETRIEVAL_TOP_K,
        "rate_limit_rpm": settings.RATE_LIMIT_PER_MINUTE,
        "openclaw_gateway_token_set": bool(settings.OPENCLAW_GATEWAY_TOKEN),
        "openclaw_ws_auth_keys_count": len(openclaw_ws_valid_tokens()),
        "audit_enabled": settings.AUDIT_ENABLED,
        "whatsapp_bridge_configured": bool(settings.WHATSAPP_BRIDGE_SECRET),
        "jwt_secret_set": bool(settings.JWT_SECRET),
    }
