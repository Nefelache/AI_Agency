"""
Email Handler — hot-pluggable skill for composing and sending email.

Drop this file into skills_layer/tools/ and it self-registers.
Delete it and the system keeps running — zero coupling to the trunk.
"""

from __future__ import annotations

from typing import Any

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.tools import register


@register
class EmailHandler(Skill):
    name = "email"
    description = "Compose and send an email via the configured SMTP relay."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")

        if not to or not subject:
            return {"success": False, "reason": "Missing 'to' or 'subject'."}

        # --- Actual send logic placeholder ---
        # Replace with smtplib / SendGrid / Resend integration.
        return {
            "success": True,
            "message": f"Email queued → {to} | Subject: {subject}",
        }
