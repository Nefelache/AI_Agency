"""
Email Handler — send email via SMTP and read recent messages via IMAP.

Configure via environment variables:
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD
  IMAP_HOST, IMAP_PORT (default 993), IMAP_USER, IMAP_PASSWORD
  EMAIL_FROM            (default = SMTP_USER)
"""

from __future__ import annotations

import email as email_lib
import imaplib
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.tools import register

_SMTP_HOST = os.getenv("SMTP_HOST", "")
_SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
_SMTP_USER = os.getenv("SMTP_USER", "")
_SMTP_PASS = os.getenv("SMTP_PASSWORD", "")
_FROM_ADDR = os.getenv("EMAIL_FROM", _SMTP_USER)

_IMAP_HOST = os.getenv("IMAP_HOST", "")
_IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
_IMAP_USER = os.getenv("IMAP_USER", _SMTP_USER)
_IMAP_PASS = os.getenv("IMAP_PASSWORD", _SMTP_PASS)


@register
class EmailHandler(Skill):
    name = "email"
    description = (
        "Send or read email. "
        "Params: action ('send'|'read'|'search'), "
        "to (str, for send), subject (str), body (str), html (bool, optional), "
        "folder ('INBOX', for read), limit (int, for read, default 10), "
        "query (str, for search)."
    )
    skill_instructions = """
When to use: user explicitly wants to send mail, list inbox, or search mail.
action=send: required to, subject, body (html optional bool).
action=read: optional folder (default INBOX), limit (default 10).
action=search: required query string.
If SMTP/IMAP not configured, the skill will fail — explain that to the user after.
"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action", "send").lower()
        if action == "send":
            return self._send(params)
        elif action == "read":
            return self._read(params)
        elif action == "search":
            return self._search(params)
        else:
            return {"success": False, "reason": f"Unknown action: {action}"}

    # ── Send ──────────────────────────────────────────────────────
    def _send(self, params: dict[str, Any]) -> dict[str, Any]:
        to      = params.get("to", "").strip()
        subject = params.get("subject", "").strip()
        body    = params.get("body", "")
        html    = bool(params.get("html", False))

        if not to or not subject:
            return {"success": False, "reason": "Missing 'to' or 'subject'."}
        if not _SMTP_HOST or not _SMTP_USER:
            return {
                "success": False,
                "reason":  "SMTP not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD.",
            }
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = _FROM_ADDR
            msg["To"]      = to
            mime_type      = "html" if html else "plain"
            msg.attach(MIMEText(body, mime_type, "utf-8"))

            with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(_SMTP_USER, _SMTP_PASS)
                server.sendmail(_FROM_ADDR, [to], msg.as_string())

            return {
                "success": True,
                "output":  f"Email sent to {to} | Subject: {subject}",
            }
        except Exception as e:
            return {"success": False, "reason": str(e)}

    # ── Read ──────────────────────────────────────────────────────
    def _read(self, params: dict[str, Any]) -> dict[str, Any]:
        folder = params.get("folder", "INBOX")
        limit  = int(params.get("limit", 10))
        return self._imap_fetch(folder, "ALL", limit)

    def _search(self, params: dict[str, Any]) -> dict[str, Any]:
        query  = params.get("query", "").strip()
        folder = params.get("folder", "INBOX")
        limit  = int(params.get("limit", 10))
        if not query:
            return {"success": False, "reason": "Missing 'query' for search."}
        imap_search = f'SUBJECT "{query}"'
        return self._imap_fetch(folder, imap_search, limit)

    def _imap_fetch(self, folder: str, criteria: str, limit: int) -> dict[str, Any]:
        if not _IMAP_HOST or not _IMAP_USER:
            return {
                "success": False,
                "reason":  "IMAP not configured. Set IMAP_HOST, IMAP_USER, IMAP_PASSWORD.",
            }
        try:
            with imaplib.IMAP4_SSL(_IMAP_HOST, _IMAP_PORT) as mail:
                mail.login(_IMAP_USER, _IMAP_PASS)
                mail.select(folder, readonly=True)
                _, data = mail.search(None, criteria)
                ids     = data[0].split()[-limit:]
                if not ids:
                    return {"success": True, "messages": [], "output": "No messages found."}

                messages = []
                for mid in reversed(ids):
                    _, raw = mail.fetch(mid, "(RFC822)")
                    msg    = email_lib.message_from_bytes(raw[0][1])
                    subject = msg.get("Subject", "(no subject)")
                    sender  = msg.get("From", "")
                    date    = msg.get("Date", "")
                    body    = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode("utf-8", errors="replace")[:500]
                                break
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="replace")[:500]
                    messages.append({"subject": subject, "from": sender, "date": date, "body": body})

            lines = [f"[{m['date']}] {m['from']}\n  {m['subject']}\n  {m['body'][:120]}" for m in messages]
            return {"success": True, "messages": messages, "output": "\n\n".join(lines)}
        except Exception as e:
            return {"success": False, "reason": str(e)}
