"""
HTTP Fetch — make arbitrary HTTP requests from the agent.
Useful for calling REST APIs, webhooks, and custom integrations.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.tools import register

_TIMEOUT   = 12
_MAX_BYTES = 32_768  # 32 KB response cap


@register
class HttpFetch(Skill):
    name = "http_fetch"
    description = (
        "Make an HTTP request to any URL. "
        "Params: url (str), method ('GET'|'POST'|'PUT'|'DELETE', default GET), "
        "headers (dict, optional), body (str|dict, optional), "
        "timeout (int seconds, optional)."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        url     = params.get("url", "").strip()
        method  = params.get("method", "GET").upper()
        headers = params.get("headers") or {}
        body    = params.get("body")
        timeout = int(params.get("timeout", _TIMEOUT))

        if not url:
            return {"success": False, "reason": "Missing 'url'."}
        if not url.startswith(("http://", "https://")):
            return {"success": False, "reason": "URL must start with http:// or https://"}

        try:
            data = None
            if body is not None:
                if isinstance(body, dict):
                    data = json.dumps(body).encode()
                    headers.setdefault("Content-Type", "application/json")
                else:
                    data = str(body).encode()

            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("User-Agent", "AgentOS/1.0")
            for k, v in headers.items():
                req.add_header(str(k), str(v))

            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status       = resp.getcode()
                resp_headers = dict(resp.headers)
                raw          = resp.read(_MAX_BYTES).decode("utf-8", errors="replace")

            # Try JSON parse for cleaner output
            try:
                parsed = json.loads(raw)
                output = json.dumps(parsed, ensure_ascii=False, indent=2)[:2000]
            except (json.JSONDecodeError, ValueError):
                output = raw[:2000]

            return {
                "success":        True,
                "status":         status,
                "content_type":   resp_headers.get("Content-Type", ""),
                "body":           output,
                "output":         f"HTTP {status} from {url}\n\n{output}",
            }
        except urllib.error.HTTPError as e:
            body_text = e.read(_MAX_BYTES).decode("utf-8", errors="replace")
            return {"success": False, "status": e.code, "reason": str(e), "body": body_text[:500]}
        except Exception as e:
            return {"success": False, "reason": str(e)}
