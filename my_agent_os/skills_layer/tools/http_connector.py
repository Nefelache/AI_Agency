"""
HTTP Connector — same execution as http_fetch, separate skill name for
per-client integration configs (alias entry point).
"""

from __future__ import annotations

from typing import Any

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.tools import register
from my_agent_os.skills_layer.tools.http_fetch import HttpFetch


@register
class HttpConnectorSkill(Skill):
    name = "http_connector"
    description = (
        "Call a REST API (integration connector). "
        "Params: url (str), method, headers (dict), body (str|dict), timeout (int)."
    )
    skill_instructions = """
When to use: user or operator asked to hit a specific business API (ERP, webhook, partner) with explicit URL.
Required: url (https). Same parameters as http_fetch.
Use only URLs the user or client configuration provided; do not guess internal endpoints.
"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        return await HttpFetch().execute(params)
