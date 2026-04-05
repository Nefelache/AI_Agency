"""
Reminder — schedule one-off or recurring in-process reminders.

Because the agent runs as a long-lived FastAPI process, reminders are
held in memory and dispatched via asyncio.  On restart they are cleared;
for persistence see the AGENT_REMINDER_PERSIST env var (future work).

Params for 'set':
  message   (str)          — what to remind
  in_seconds (int)         — delay from now
  in_minutes (int)         — convenience alias
  repeat_seconds (int)     — if set, repeats every N seconds (max 24h)

Params for 'list' / 'cancel':
  reminder_id (str)        — returned by 'set', required for 'cancel'
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.tools import register

# Global reminder store: id → {message, fire_at, task, repeat_sec}
_reminders: dict[str, dict] = {}


@register
class Reminder(Skill):
    name = "reminder"
    description = (
        "Schedule reminders. "
        "Params: action ('set'|'list'|'cancel'), "
        "message (str), in_seconds (int), in_minutes (int), repeat_seconds (int), "
        "reminder_id (str, for cancel)."
    )
    skill_instructions = """
When to use: user wants a timed reminder (提醒, remind me, in X minutes).
action=set: required message; required in_seconds OR in_minutes (>0).
action=list: no extra fields.
action=cancel: required reminder_id from a prior list/set.
"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action", "set").lower()
        if action == "set":
            return self._set(params)
        elif action == "list":
            return self._list()
        elif action == "cancel":
            return self._cancel(params.get("reminder_id", ""))
        else:
            return {"success": False, "reason": f"Unknown action: {action}"}

    def _set(self, params: dict[str, Any]) -> dict[str, Any]:
        message = params.get("message", "").strip()
        if not message:
            return {"success": False, "reason": "Missing 'message'."}

        delay = int(params.get("in_seconds", 0)) + int(params.get("in_minutes", 0)) * 60
        if delay <= 0:
            return {"success": False, "reason": "Provide 'in_seconds' or 'in_minutes' (> 0)."}
        delay = min(delay, 86400)  # cap at 24h

        repeat_sec = int(params.get("repeat_seconds", 0))
        rid   = str(uuid.uuid4())[:8]
        fire_at = datetime.now(timezone.utc).timestamp() + delay

        # Schedule via asyncio if an event loop is running
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = loop.create_task(_fire_reminder(rid, message, delay, repeat_sec))
            else:
                task = None
        except RuntimeError:
            task = None

        _reminders[rid] = {
            "message":    message,
            "fire_at":    fire_at,
            "repeat_sec": repeat_sec,
            "task":       task,
        }
        return {
            "success":     True,
            "reminder_id": rid,
            "fires_in":    f"{delay}s",
            "output":      f"Reminder set: '{message}' in {delay}s (id: {rid})",
        }

    def _list(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc).timestamp()
        rows = []
        for rid, r in list(_reminders.items()):
            remaining = max(0, int(r["fire_at"] - now))
            rows.append({"id": rid, "message": r["message"], "remaining_s": remaining})
        text = "\n".join(f"[{r['id']}] '{r['message']}' in {r['remaining_s']}s" for r in rows) or "No active reminders."
        return {"success": True, "reminders": rows, "output": text}

    def _cancel(self, rid: str) -> dict[str, Any]:
        if rid not in _reminders:
            return {"success": False, "reason": f"Reminder '{rid}' not found."}
        r = _reminders.pop(rid)
        if r.get("task"):
            r["task"].cancel()
        return {"success": True, "output": f"Reminder '{rid}' cancelled."}


async def _push_whatsapp(message: str) -> None:
    """Push reminder to the owner's WhatsApp number via the agent-os bridge."""
    owner_number = os.getenv("WHATSAPP_ALLOW_FROM", "").split(",")[0].strip()
    agent_url    = os.getenv("AGENT_OS_URL", "http://localhost:8000")
    secret       = os.getenv("WHATSAPP_BRIDGE_SECRET", "")
    if not owner_number:
        return
    try:
        import urllib.request as _req
        import json as _json
        payload = _json.dumps({"to": owner_number, "message": f"⏰ Reminder: {message}"}).encode()
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["X-Bridge-Secret"] = secret
        req = _req.Request(f"{agent_url}/reminder/push", data=payload, headers=headers, method="POST")
        with _req.urlopen(req, timeout=5):
            pass
    except Exception as exc:
        logging.getLogger(__name__).warning("Reminder WhatsApp push failed: %s", exc)


async def _fire_reminder(rid: str, message: str, delay: float, repeat_sec: int) -> None:
    await asyncio.sleep(delay)
    logging.getLogger(__name__).info("REMINDER [%s]: %s", rid, message)
    await _push_whatsapp(message)
    if rid in _reminders:
        _reminders[rid]["fire_at"] = datetime.now(timezone.utc).timestamp() + max(repeat_sec, 0)
    if repeat_sec > 0:
        await _fire_reminder(rid, message, repeat_sec, repeat_sec)
    else:
        _reminders.pop(rid, None)
