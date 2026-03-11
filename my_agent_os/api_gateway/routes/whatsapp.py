"""
WhatsApp Channel — OpenClaw-style integration.

Supports two modes:
  1. WhatsApp Web (Baileys) — QR code login, personal/dedicated number
  2. WhatsApp Cloud API (PyWa) — Official Meta Business API, webhook

Inbound flow:
  - Baileys bridge POSTs to /whatsapp/inbound
  - PyWa webhook receives at /whatsapp/webhook (Cloud API)
  - Both route to agent, apply DM/group policies, return reply
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from my_agent_os.auth.dependencies import rate_limit_check
from my_agent_os.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"], dependencies=[Depends(rate_limit_check)])


def _normalize_phone(phone: str) -> str:
    """Normalize to E.164-style for allowlist matching."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("0"):
        digits = digits[1:]
    if len(digits) >= 10 and not digits.startswith("1"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    return "+" + digits if digits else phone


def _check_dm_policy(phone: str, is_group: bool) -> tuple[bool, str | None]:
    """
    Check if sender is allowed. Returns (allowed, error_message).
    OpenClaw-style: allowlist, pairing, open, disabled.
    """
    from my_agent_os.config.channel_policies import get_whatsapp_config

    cfg = get_whatsapp_config()
    if not cfg.get("enabled", True):
        return False, "WhatsApp channel disabled"

    normalized = _normalize_phone(phone)
    dm_policy = cfg.get("dm_policy", "allowlist")
    allow_from = cfg.get("allow_from") or []
    # Support env override
    env_allow = getattr(settings, "WHATSAPP_ALLOW_FROM", "") or ""
    if env_allow:
        allow_from = [s.strip() for s in env_allow.split(",") if s.strip()] or allow_from

    if dm_policy == "disabled":
        return False, "WhatsApp DMs disabled"

    if dm_policy == "open":
        if "*" in allow_from or not allow_from:
            return True, None
        return False, "open policy requires allowFrom: ['*']"

    if dm_policy in ("allowlist", "pairing"):
        if normalized in allow_from:
            return True, None
        # Allow paired numbers from store
        from my_agent_os.config.channel_policies import is_paired

        if is_paired("whatsapp", normalized):
            return True, None
        if dm_policy == "pairing":
            return False, "pairing_required"  # Signal to send pairing code
        return False, "Sender not in allowlist"

    return False, "Unknown policy"


def _chunk_text(text: str, limit: int = 4000, mode: str = "newline") -> list[str]:
    """Split long text for WhatsApp delivery."""
    if len(text) <= limit:
        return [text] if text else []

    chunks = []
    if mode == "newline":
        paragraphs = text.split("\n\n")
        current = ""
        for p in paragraphs:
            if len(current) + len(p) + 2 <= limit:
                current += ("\n\n" if current else "") + p
            else:
                if current:
                    chunks.append(current)
                if len(p) <= limit:
                    current = p
                else:
                    for i in range(0, len(p), limit):
                        chunks.append(p[i : i + limit])
                    current = ""
        if current:
            chunks.append(current)
    else:
        for i in range(0, len(text), limit):
            chunks.append(text[i : i + limit])
    return chunks


async def _route_to_agent(raw_input: str, user_id: str, channel: str = "whatsapp") -> dict[str, Any]:
    from my_agent_os.agent_core.router_engine import route

    return await route(
        raw_input=raw_input,
        channel=channel,
        user_id=user_id,
        with_memory=True,
        force_crew=False,
    )


# --- Baileys bridge webhook (WhatsApp Web / QR login) ---


class WhatsAppInbound(BaseModel):
    """Payload from Baileys bridge."""

    from_number: str
    from_name: str | None = None
    message: str
    message_id: str | None = None
    is_group: bool = False
    group_id: str | None = None
    group_name: str | None = None


@router.post("/inbound")
async def handle_whatsapp_inbound(
    payload: WhatsAppInbound,
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    x_whatsapp_secret: str | None = Header(None, alias="X-WhatsApp-Secret"),
):
    """
    Receive messages from Baileys bridge. Auth via X-API-Key (channel) or X-WhatsApp-Secret.
    """
    # Auth: API_KEY_CHANNEL or WHATSAPP_BRIDGE_SECRET
    secret = getattr(settings, "WHATSAPP_BRIDGE_SECRET", "") or ""
    if x_whatsapp_secret and secret and x_whatsapp_secret == secret:
        pass  # OK
    elif x_api_key and x_api_key == settings.API_KEY_CHANNEL:
        pass  # OK
    elif x_api_key and x_api_key == settings.API_KEY_OWNER:
        pass  # OK
    else:
        raise HTTPException(401, "Invalid or missing WhatsApp bridge auth")

    allowed, err = _check_dm_policy(payload.from_number, payload.is_group)
    if not allowed:
        if err == "pairing_required":
            return {"action": "pairing_required", "reply": None}
        logger.warning("WhatsApp policy denied: %s from %s", err, payload.from_number)
        return {"action": "denied", "reply": None}

    user_id = f"whatsapp:{_normalize_phone(payload.from_number)}"
    if payload.is_group and payload.group_id:
        user_id = f"whatsapp:group:{payload.group_id}:{_normalize_phone(payload.from_number)}"

    result = await _route_to_agent(payload.message, user_id)

    answer = result.get("answer") or result.get("brief") or ""
    if not answer:
        answer = "Done."

    from my_agent_os.config.channel_policies import get_whatsapp_config

    cfg = get_whatsapp_config()
    limit = cfg.get("text_chunk_limit", 4000)
    mode = cfg.get("chunk_mode", "newline")
    chunks = _chunk_text(answer, limit, mode)

    return {
        "action": "reply",
        "reply": chunks,
        "reply_single": answer if len(chunks) <= 1 else None,
    }
