"""
Slack Channel — receives slash commands and event webhooks from Slack.

Implements the Slack slash-command handler pattern:
  POST /slack/command    — /agentOS slash command (responds in 3s)
  POST /slack/events     — Slack Events API (url_verification + message events)
  POST /slack/interact   — Slack interactive components (button clicks)

Setup:
  1. Create a Slack App at https://api.slack.com/apps
  2. Add slash command /agentOS pointing to POST /slack/command
  3. Subscribe to bot_message + app_mention events (POST /slack/events)
  4. Set SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET in .env

Environment:
  SLACK_BOT_TOKEN      — xoxb-...
  SLACK_SIGNING_SECRET — from Basic Information → App Credentials
  SLACK_CHANNEL_ID     — default channel to post to (optional)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from my_agent_os.config.settings import settings

router = APIRouter(prefix="/slack", tags=["Slack"])

_SLACK_TOKEN   = os.getenv("SLACK_BOT_TOKEN", "")
_SLACK_SECRET  = os.getenv("SLACK_SIGNING_SECRET", "")
_SLACK_CHANNEL = os.getenv("SLACK_CHANNEL_ID", "")
_SLACK_API     = "https://slack.com/api"

_router_ref = None


def set_router(route_fn) -> None:
    global _router_ref
    _router_ref = route_fn


# ── Signature verification ────────────────────────────────────────

def _verify_slack(body: bytes, timestamp: str, signature: str) -> bool:
    if not _SLACK_SECRET:
        return True  # skip verification if not configured
    if abs(time.time() - float(timestamp)) > 300:
        return False  # replay attack guard
    base_string = f"v0:{timestamp}:".encode() + body
    computed    = "v0=" + hmac.new(_SLACK_SECRET.encode(), base_string, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


# ── Slack Web API post ────────────────────────────────────────────

def _slack_post(method: str, data: dict) -> dict:
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{_SLACK_API}/{method}",
        data=payload,
        headers={
            "Authorization": f"Bearer {_SLACK_TOKEN}",
            "Content-Type":  "application/json;charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


async def _agent_reply(user_id: str, text: str) -> str:
    """Forward message to CoreClaw router and return answer."""
    if not _router_ref:
        return "(router not initialized)"
    try:
        result = await _router_ref(
            raw_input=text,
            channel="slack",
            user_id=f"slack:{user_id}",
            with_memory=True,
            force_crew=False,
        )
        return result.get("brief") or result.get("answer") or "(no response)"
    except Exception as e:
        return f"CoreClaw error: {e}"


# ── Routes ───────────────────────────────────────────────────────

@router.post("/command")
async def slash_command(
    request: Request,
    x_slack_request_timestamp: str | None = Header(None),
    x_slack_signature:         str | None = Header(None),
) -> Any:
    """
    Handles /agentOS slash command.
    Slack requires a response within 3s; we reply immediately with
    an ephemeral ack, then post the full answer asynchronously.
    """
    body = await request.body()

    if not _verify_slack(body, x_slack_request_timestamp or "0", x_slack_signature or ""):
        raise HTTPException(403, "Invalid Slack signature.")

    params   = dict(urllib.parse.parse_qsl(body.decode()))
    text     = params.get("text", "").strip()
    user_id  = params.get("user_id", "unknown")
    channel  = params.get("channel_id", _SLACK_CHANNEL)
    response_url = params.get("response_url", "")

    if not text:
        return PlainTextResponse("Usage: /agentOS <your message>")

    # Async post back via response_url to avoid 3s timeout
    import asyncio
    asyncio.create_task(_deferred_slack_reply(user_id, text, channel, response_url))

    return JSONResponse({
        "response_type": "ephemeral",
        "text":          f"⏳ Processing: _{text[:80]}_",
    })


async def _deferred_slack_reply(user_id: str, text: str, channel: str, response_url: str) -> None:
    import asyncio
    answer = await _agent_reply(user_id, text)

    payload = json.dumps({
        "response_type": "in_channel",
        "text": answer[:3000],
    }).encode()

    try:
        if response_url:
            req = urllib.request.Request(
                response_url, data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
        elif channel and _SLACK_TOKEN:
            _slack_post("chat.postMessage", {
                "channel": channel,
                "text":    f"<@{user_id}> → {text[:80]}\n\n{answer[:2800]}",
            })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Slack deferred reply failed: %s", e)


@router.post("/events")
async def events_api(
    request: Request,
    x_slack_request_timestamp: str | None = Header(None),
    x_slack_signature:         str | None = Header(None),
) -> Any:
    body = await request.body()

    if not _verify_slack(body, x_slack_request_timestamp or "0", x_slack_signature or ""):
        raise HTTPException(403, "Invalid Slack signature.")

    event = json.loads(body)
    event_type = event.get("type", "")

    # URL verification challenge
    if event_type == "url_verification":
        return PlainTextResponse(event.get("challenge", ""))

    # Handle app_mention and DMs
    inner = event.get("event", {})
    if inner.get("type") in ("app_mention", "message") and not inner.get("bot_id"):
        user_id  = inner.get("user", "unknown")
        text     = inner.get("text", "").replace(f"<@{event.get('authorizations',[{}])[0].get('user_id','')}> ", "").strip()
        channel  = inner.get("channel", _SLACK_CHANNEL)
        if text:
            import asyncio
            asyncio.create_task(_post_slack_reply(user_id, text, channel))

    return JSONResponse({"ok": True})


async def _post_slack_reply(user_id: str, text: str, channel: str) -> None:
    answer = await _agent_reply(user_id, text)
    if channel and _SLACK_TOKEN:
        try:
            _slack_post("chat.postMessage", {
                "channel": channel,
                "text":    answer[:3000],
            })
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Slack post failed: %s", e)


@router.post("/interact")
async def interact(
    request: Request,
    x_slack_request_timestamp: str | None = Header(None),
    x_slack_signature:         str | None = Header(None),
) -> Any:
    """Handle Slack Block Kit button interactions (future use)."""
    body = await request.body()
    if not _verify_slack(body, x_slack_request_timestamp or "0", x_slack_signature or ""):
        raise HTTPException(403, "Invalid Slack signature.")
    return JSONResponse({"ok": True})
