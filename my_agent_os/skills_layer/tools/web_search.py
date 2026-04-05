"""
Web Search — real-time web search with provider priority:
  1. Tavily  (TAVILY_API_KEY)  — best results, AI-optimised, 1000 req/mo free
  2. SerpAPI (SERPAPI_KEY)     — Google results, 100 req/mo free
  3. DuckDuckGo Instant Answer — zero-key fallback (limited to wiki-style results)
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.tools import register


@register
class WebSearch(Skill):
    name = "web_search"
    description = "Search the web for real-time information. Params: query (str), num_results (int, optional, default 5)."
    skill_instructions = """
When to use: user wants current/public information (news, prices, facts, "search for X", 搜索).
Required: query — concise search string in the user's language (not an empty string).
Optional: num_results (int, default 5).
If the user only says "search" with no topic, do NOT call this skill — ask what to search.
"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("query", "").strip()
        num   = int(params.get("num_results", 5))
        if not query:
            return {"success": False, "reason": "Missing 'query'."}

        tavily_key  = os.getenv("TAVILY_API_KEY", "")
        serpapi_key = os.getenv("SERPAPI_KEY", "")
        if tavily_key:
            return self._tavily_search(query, num, tavily_key)
        if serpapi_key:
            return self._serpapi_search(query, num, serpapi_key)
        return self._ddg_search(query, num)

    # ── Tavily (recommended — AI-optimised search) ───────────────
    def _tavily_search(self, query: str, num: int, key: str) -> dict[str, Any]:
        try:
            payload = json.dumps({
                "api_key": key,
                "query": query,
                "search_depth": "basic",
                "max_results": num,
                "include_answer": True,
            }).encode()
            req = urllib.request.Request(
                "https://api.tavily.com/search",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            results = [
                {
                    "title":   r.get("title", ""),
                    "snippet": r.get("content", "")[:300],
                    "url":     r.get("url", ""),
                }
                for r in data.get("results", [])[:num]
            ]
            direct_answer = data.get("answer", "")
            output = _format_results(query, results)
            if direct_answer:
                output = f"Direct answer: {direct_answer}\n\n{output}"
            return {"success": True, "query": query, "results": results, "output": output}
        except Exception as e:
            return {"success": False, "reason": str(e)}

    # ── DuckDuckGo Instant Answer (zero-key fallback) ────────────
    def _ddg_search(self, query: str, num: int) -> dict[str, Any]:
        try:
            q = urllib.parse.quote_plus(query)
            url = f"https://api.duckduckgo.com/?q={q}&format=json&no_redirect=1&no_html=1&skip_disambig=1"
            req = urllib.request.Request(url, headers={"User-Agent": "AgentOS/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())

            results = []
            abstract = data.get("AbstractText", "")
            if abstract:
                results.append({
                    "title":   data.get("Heading", query),
                    "snippet": abstract[:400],
                    "url":     data.get("AbstractURL", ""),
                })

            for topic in data.get("RelatedTopics", [])[:num]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append({
                        "title":   topic.get("Text", "")[:80],
                        "snippet": topic.get("Text", "")[:300],
                        "url":     topic.get("FirstURL", ""),
                    })

            return {
                "success": True,
                "query":   query,
                "results": results[:num],
                "output":  _format_results(query, results[:num]),
            }
        except Exception as e:
            return {"success": False, "reason": str(e)}

    # ── SerpAPI (optional, richer results) ──────────────────────
    def _serpapi_search(self, query: str, num: int, key: str) -> dict[str, Any]:
        try:
            q   = urllib.parse.quote_plus(query)
            url = f"https://serpapi.com/search.json?q={q}&num={num}&api_key={key}"
            req = urllib.request.Request(url, headers={"User-Agent": "AgentOS/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            organics = data.get("organic_results", [])
            results = [
                {
                    "title":   r.get("title", ""),
                    "snippet": r.get("snippet", ""),
                    "url":     r.get("link", ""),
                }
                for r in organics[:num]
            ]
            return {
                "success": True,
                "query":   query,
                "results": results,
                "output":  _format_results(query, results),
            }
        except Exception as e:
            return {"success": False, "reason": str(e)}


def _format_results(query: str, results: list[dict]) -> str:
    if not results:
        return f"No results found for: {query}"
    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
    return "\n".join(lines)
