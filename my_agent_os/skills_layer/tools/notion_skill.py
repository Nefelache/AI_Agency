"""
Notion Skill — read and write Notion pages and databases.

Requires:
  NOTION_API_KEY  — Notion Integration token (secret_xxx)
  NOTION_DB_ID    — default database ID (optional, can be passed per-call)

OpenClaw parity: search, read page, append blocks, create page in database.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.tools import register

_NOTION_KEY    = os.getenv("NOTION_API_KEY", "")
_NOTION_DB_ID  = os.getenv("NOTION_DB_ID", "")
_NOTION_BASE   = "https://api.notion.com/v1"
_NOTION_VER    = "2022-06-28"


def _headers() -> dict:
    return {
        "Authorization":   f"Bearer {_NOTION_KEY}",
        "Notion-Version":  _NOTION_VER,
        "Content-Type":    "application/json",
    }


def _api(method: str, path: str, body: dict | None = None) -> dict:
    url  = _NOTION_BASE + path
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, method=method, headers=_headers())
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode())


def _extract_text(rich_text: list) -> str:
    return "".join(t.get("plain_text", "") for t in rich_text)


@register
class NotionSkill(Skill):
    name = "notion"
    description = (
        "Interact with Notion. "
        "Params: action ('search'|'read'|'append'|'create'), "
        "query (str, for search), page_id (str, for read/append), "
        "database_id (str, for create), title (str), content (str), "
        "properties (dict, optional)."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if not _NOTION_KEY:
            return {"success": False, "reason": "NOTION_API_KEY not set."}

        action = params.get("action", "search").lower()
        if action == "search":
            return self._search(params)
        elif action == "read":
            return self._read_page(params)
        elif action == "append":
            return self._append_blocks(params)
        elif action == "create":
            return self._create_page(params)
        else:
            return {"success": False, "reason": f"Unknown action: {action}"}

    # ── Search ────────────────────────────────────────────────────
    def _search(self, params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("query", "").strip()
        try:
            data = _api("POST", "/search", {
                "query": query,
                "sort": {"direction": "descending", "timestamp": "last_edited_time"},
                "page_size": int(params.get("limit", 10)),
            })
            results = []
            for obj in data.get("results", []):
                title = ""
                if obj["object"] == "page":
                    props = obj.get("properties", {})
                    for v in props.values():
                        if v.get("type") == "title":
                            title = _extract_text(v.get("title", []))
                            break
                elif obj["object"] == "database":
                    title = _extract_text(obj.get("title", []))
                results.append({
                    "id":     obj["id"],
                    "type":   obj["object"],
                    "title":  title,
                    "url":    obj.get("url", ""),
                })
            lines = [f"[{r['type']}] {r['title']} — {r['url']}" for r in results]
            return {"success": True, "results": results, "output": "\n".join(lines) or "No results."}
        except Exception as e:
            return {"success": False, "reason": str(e)}

    # ── Read page ─────────────────────────────────────────────────
    def _read_page(self, params: dict[str, Any]) -> dict[str, Any]:
        page_id = params.get("page_id", "").replace("-", "").strip()
        if not page_id:
            return {"success": False, "reason": "Missing 'page_id'."}
        try:
            page   = _api("GET", f"/pages/{page_id}")
            blocks = _api("GET", f"/blocks/{page_id}/children?page_size=50")

            lines = []
            for block in blocks.get("results", []):
                btype = block.get("type", "")
                bdata = block.get(btype, {})
                rich  = bdata.get("rich_text", [])
                text  = _extract_text(rich)
                if text:
                    prefix = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
                              "bulleted_list_item": "• ", "numbered_list_item": "1. "}.get(btype, "")
                    lines.append(prefix + text)

            content = "\n".join(lines)
            return {
                "success": True,
                "page_id": page_id,
                "url":     page.get("url", ""),
                "content": content,
                "output":  content or "(empty page)",
            }
        except Exception as e:
            return {"success": False, "reason": str(e)}

    # ── Append blocks ─────────────────────────────────────────────
    def _append_blocks(self, params: dict[str, Any]) -> dict[str, Any]:
        page_id = params.get("page_id", "").replace("-", "").strip()
        content = params.get("content", "").strip()
        if not page_id or not content:
            return {"success": False, "reason": "Missing 'page_id' or 'content'."}
        try:
            blocks = [
                {
                    "object": "block",
                    "type":   "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": para}}]
                    },
                }
                for para in content.split("\n\n")
                if para.strip()
            ]
            _api("PATCH", f"/blocks/{page_id}/children", {"children": blocks})
            return {"success": True, "output": f"Appended {len(blocks)} block(s) to page {page_id[:8]}."}
        except Exception as e:
            return {"success": False, "reason": str(e)}

    # ── Create page in database ───────────────────────────────────
    def _create_page(self, params: dict[str, Any]) -> dict[str, Any]:
        db_id  = params.get("database_id", _NOTION_DB_ID).replace("-", "").strip()
        title  = params.get("title", "Untitled").strip()
        content = params.get("content", "")
        props   = params.get("properties", {})
        if not db_id:
            return {"success": False, "reason": "Missing 'database_id' (or set NOTION_DB_ID)."}
        try:
            body: dict[str, Any] = {
                "parent": {"database_id": db_id},
                "properties": {
                    "Name": {"title": [{"text": {"content": title}}]},
                    **props,
                },
            }
            if content:
                body["children"] = [
                    {
                        "object": "block",
                        "type":   "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": content[:2000]}}]},
                    }
                ]
            page = _api("POST", "/pages", body)
            return {
                "success": True,
                "page_id": page["id"],
                "url":     page.get("url", ""),
                "output":  f"Created Notion page: '{title}' → {page.get('url', page['id'])}",
            }
        except Exception as e:
            return {"success": False, "reason": str(e)}
