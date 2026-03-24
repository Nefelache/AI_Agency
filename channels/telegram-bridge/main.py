"""
Telegram Bridge — polls the Telegram Bot API and forwards messages to Agent OS.

Pattern mirrors the WhatsApp Baileys bridge:
  - Long-polls /getUpdates
  - Forwards allowed user messages to POST /mobile/webhook
  - Sends Agent OS responses back via sendMessage

Environment:
  TELEGRAM_BOT_TOKEN   — from @BotFather
  AGENT_OS_URL         — http://localhost:8000
  AGENT_OS_SECRET      — X-API-Key to use when calling Agent OS
  TELEGRAM_ALLOW_FROM  — comma-separated Telegram user IDs (integers) or usernames

Usage:
  python main.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TG] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
AGENT_URL     = os.getenv("AGENT_OS_URL", "http://127.0.0.1:8000")
AGENT_SECRET  = os.getenv("AGENT_OS_SECRET", "")
ALLOW_FROM    = set(
    x.strip() for x in os.getenv("TELEGRAM_ALLOW_FROM", "").split(",") if x.strip()
)

TG_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ── Telegram API helpers ──────────────────────────────────────────

def tg_get(method: str, params: dict | None = None) -> dict:
    url  = f"{TG_BASE}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "AgentOS-TG/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def tg_post(method: str, data: dict) -> dict:
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{TG_BASE}/{method}",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "AgentOS-TG/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def send_message(chat_id: int, text: str) -> None:
    try:
        tg_post("sendMessage", {
            "chat_id":    chat_id,
            "text":       text[:4096],
            "parse_mode": "Markdown",
        })
    except Exception as e:
        logger.error("sendMessage failed: %s", e)


# ── Agent OS forwarding ───────────────────────────────────────────

def forward_to_agent(user_id: str, text: str) -> str:
    payload = json.dumps({
        "text":    text,
        "user_id": f"telegram:{user_id}",
        "channel": "telegram",
    }).encode()
    req = urllib.request.Request(
        f"{AGENT_URL}/mobile/webhook",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-API-Key":    AGENT_SECRET,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return data.get("brief") or data.get("answer") or "(no response)"
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error("Agent OS error %s: %s", e.code, body)
        return f"Agent OS returned {e.code}."
    except Exception as e:
        logger.error("Forward failed: %s", e)
        return "Could not reach Agent OS."


# ── Allow-list check ──────────────────────────────────────────────

def is_allowed(update: dict) -> bool:
    if not ALLOW_FROM:
        return True  # open mode if no allowlist
    msg = update.get("message", {})
    uid = str(msg.get("from", {}).get("id", ""))
    username = msg.get("from", {}).get("username", "")
    return uid in ALLOW_FROM or username in ALLOW_FROM or f"@{username}" in ALLOW_FROM


# ── Main polling loop ─────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set. Exiting.")
        sys.exit(1)

    logger.info("Agent OS Telegram bridge starting…")
    me = tg_get("getMe")
    logger.info("Bot: @%s (id %s)", me["result"]["username"], me["result"]["id"])

    offset = 0
    while True:
        try:
            result = tg_get("getUpdates", {"offset": offset, "timeout": 25, "limit": 100})
            for update in result.get("result", []):
                offset = update["update_id"] + 1
                msg    = update.get("message", {})
                text   = msg.get("text", "").strip()
                chat_id = msg.get("chat", {}).get("id")

                if not text or not chat_id:
                    continue

                if not is_allowed(update):
                    logger.info("Blocked update from %s", msg.get("from", {}).get("id"))
                    continue

                user_id  = str(msg["from"]["id"])
                username = msg["from"].get("username", user_id)
                logger.info("Message from @%s: %s", username, text[:80])

                # Handle /start command
                if text.startswith("/start"):
                    send_message(chat_id,
                        "👋 *Agent OS* connected.\n\nSend me any message and your AI agent will reply."
                    )
                    continue

                # Forward to Agent OS and reply
                reply = forward_to_agent(user_id, text)
                send_message(chat_id, reply)

        except KeyboardInterrupt:
            logger.info("Shutting down.")
            break
        except Exception as e:
            logger.error("Poll error: %s — retrying in 5s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
