"""
Web Search — real-time web search with provider priority:
  1. Tavily  (TAVILY_API_KEY)  — best results, AI-optimised, 1000 req/mo free
  2. SerpAPI (SERPAPI_KEY)     — Google results, 100 req/mo free
  3. DuckDuckGo Instant Answer — zero-key fallback (limited to wiki-style results)
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.base import skill_err, skill_ok
from my_agent_os.skills_layer.tools import register

_SEARCH_DIAGNOSTICS: dict[str, Any] = {
    "total_requests": 0,
    "last_success_provider": None,
    "last_success_ts": None,
    "last_failure": None,
}


def get_web_search_diagnostics() -> dict[str, Any]:
    """Expose lightweight runtime diagnostics for /health endpoints."""
    return dict(_SEARCH_DIAGNOSTICS)


@register
class WebSearch(Skill):
    name = "web_search"
    description = "Search the web for real-time information. Params: query (str), num_results (int, optional, default 5)."

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("query", "").strip()
        num = max(1, min(int(params.get("num_results", 5)), 10))
        if not query:
            _mark_search_failure("none", "INVALID_PARAMS", query)
            return skill_err("INVALID_PARAMS", "Missing 'query'.")

        _SEARCH_DIAGNOSTICS["total_requests"] = int(_SEARCH_DIAGNOSTICS.get("total_requests", 0)) + 1
        tavily_key = os.getenv("TAVILY_API_KEY", "")
        serpapi_key = os.getenv("SERPAPI_KEY", "")
        allow_ddg = os.getenv("WEB_SEARCH_ALLOW_DDG", "1") == "1"

        providers: list[tuple[str, Any]] = []
        if tavily_key:
            providers.append(("tavily", lambda: self._tavily_search(query, num, tavily_key)))
        if serpapi_key:
            providers.append(("serpapi", lambda: self._serpapi_search(query, num, serpapi_key)))
        if allow_ddg:
            providers.append(("duckduckgo", lambda: self._ddg_search(query, num)))

        if not providers:
            _mark_search_failure("none", "NO_PROVIDER", query)
            return skill_err(
                "NO_PROVIDER",
                "No search provider configured. Set TAVILY_API_KEY or SERPAPI_KEY.",
                provider="none",
            )

        failures: list[dict[str, str]] = []
        for provider, run in providers:
            result = await run()
            if result.get("ok", False):
                _mark_search_success(str(result.get("provider") or provider))
                if failures:
                    result["fallbacks"] = failures
                return result
            code = str(result.get("code", "SKILL_FAILED"))
            message = str(result.get("message", "search failed"))
            failures.append({"provider": provider, "code": code, "message": message})
            _mark_search_failure(provider, code, query)

        last = failures[-1]
        return skill_err(
            last["code"],
            last["message"],
            provider=last["provider"],
            data={"fallbacks": failures, "query": query},
        )

    # ── Tavily (recommended — AI-optimised search) ───────────────
    async def _tavily_search(self, query: str, num: int, key: str) -> dict[str, Any]:
        try:
            payload = {
                "api_key": key,
                "query": query,
                "search_depth": "basic",
                "max_results": num,
                "include_answer": True,
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                resp = await client.post("https://api.tavily.com/search", json=payload)
                resp.raise_for_status()
                data = resp.json()

            results = [
                {
                    "title": r.get("title", ""),
                    "snippet": r.get("content", "")[:300],
                    "url": r.get("url", ""),
                }
                for r in data.get("results", [])[:num]
            ]
            direct_answer = data.get("answer", "")
            if not results:
                return skill_err(
                    "EMPTY_RESULT",
                    f"No web results found for: {query}",
                    output=f"No results found for: {query}",
                    provider="tavily",
                )
            output = _format_results(query, results, provider="tavily")
            if direct_answer:
                output = f"Direct answer: {direct_answer}\n\n{output}"
            return skill_ok(
                message=f"Found {len(results)} web results.",
                output=output,
                data={"query": query, "results": results},
                provider="tavily",
            )
        except httpx.TimeoutException:
            return skill_err("PROVIDER_TIMEOUT", "Tavily request timed out.", retryable=True, provider="tavily")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response else None
            if status == 429:
                return skill_err("PROVIDER_RATE_LIMIT", "Tavily rate limit reached.", retryable=True, provider="tavily")
            return skill_err(
                "PROVIDER_ERROR",
                f"Tavily HTTP {status or 'error'}",
                retryable=bool(status and status >= 500),
                provider="tavily",
            )
        except Exception as e:
            return skill_err("PROVIDER_ERROR", str(e), retryable=True, provider="tavily")

    # ── DuckDuckGo Instant Answer (zero-key fallback) ────────────
    async def _ddg_search(self, query: str, num: int) -> dict[str, Any]:
        try:
            url = "https://api.duckduckgo.com/"
            params = {
                "q": query,
                "format": "json",
                "no_redirect": 1,
                "no_html": 1,
                "skip_disambig": 1,
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0)) as client:
                resp = await client.get(url, params=params, headers={"User-Agent": "AgentOS/1.0"})
                resp.raise_for_status()
                data = resp.json()

            results = []
            abstract = data.get("AbstractText", "")
            if abstract:
                results.append({
                    "title": data.get("Heading", query),
                    "snippet": abstract[:400],
                    "url": data.get("AbstractURL", ""),
                })

            for topic in data.get("RelatedTopics", [])[:num]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append({
                        "title": topic.get("Text", "")[:80],
                        "snippet": topic.get("Text", "")[:300],
                        "url": topic.get("FirstURL", ""),
                    })
            results = results[:num]
            if not results:
                return skill_err(
                    "EMPTY_RESULT",
                    f"No web results found for: {query}",
                    output=f"No results found for: {query}",
                    provider="duckduckgo",
                )
            return skill_ok(
                message=f"Found {len(results)} web results.",
                output=_format_results(query, results, provider="duckduckgo"),
                data={"query": query, "results": results},
                provider="duckduckgo",
            )
        except httpx.TimeoutException:
            return skill_err("PROVIDER_TIMEOUT", "DuckDuckGo request timed out.", retryable=True, provider="duckduckgo")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response else None
            if status == 429:
                return skill_err("PROVIDER_RATE_LIMIT", "DuckDuckGo rate limit reached.", retryable=True, provider="duckduckgo")
            return skill_err(
                "PROVIDER_ERROR",
                f"DuckDuckGo HTTP {status or 'error'}",
                retryable=bool(status and status >= 500),
                provider="duckduckgo",
            )
        except Exception as e:
            return skill_err("PROVIDER_ERROR", str(e), retryable=True, provider="duckduckgo")

    # ── SerpAPI (optional, richer results) ──────────────────────
    async def _serpapi_search(self, query: str, num: int, key: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                resp = await client.get(
                    "https://serpapi.com/search.json",
                    params={"q": query, "num": num, "api_key": key},
                    headers={"User-Agent": "AgentOS/1.0"},
                )
                resp.raise_for_status()
                data = resp.json()

            organics = data.get("organic_results", [])
            results = [
                {
                    "title": r.get("title", ""),
                    "snippet": r.get("snippet", ""),
                    "url": r.get("link", ""),
                }
                for r in organics[:num]
            ]
            if not results:
                return skill_err(
                    "EMPTY_RESULT",
                    f"No web results found for: {query}",
                    output=f"No results found for: {query}",
                    provider="serpapi",
                )
            return skill_ok(
                message=f"Found {len(results)} web results.",
                output=_format_results(query, results, provider="serpapi"),
                data={"query": query, "results": results},
                provider="serpapi",
            )
        except httpx.TimeoutException:
            return skill_err("PROVIDER_TIMEOUT", "SerpAPI request timed out.", retryable=True, provider="serpapi")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response else None
            if status == 429:
                return skill_err("PROVIDER_RATE_LIMIT", "SerpAPI rate limit reached.", retryable=True, provider="serpapi")
            return skill_err(
                "PROVIDER_ERROR",
                f"SerpAPI HTTP {status or 'error'}",
                retryable=bool(status and status >= 500),
                provider="serpapi",
            )
        except Exception as e:
            return skill_err("PROVIDER_ERROR", str(e), retryable=True, provider="serpapi")


def _format_results(query: str, results: list[dict], *, provider: str) -> str:
    if not results:
        return f"No results found for: {query}"
    lines = [f"Search results for: {query} ({provider})\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
    return "\n".join(lines)


def _mark_search_success(provider: str) -> None:
    _SEARCH_DIAGNOSTICS["last_success_provider"] = provider
    _SEARCH_DIAGNOSTICS["last_success_ts"] = int(time.time())
    _SEARCH_DIAGNOSTICS["last_failure"] = None


def _mark_search_failure(provider: str, code: str, query: str) -> None:
    _SEARCH_DIAGNOSTICS["last_failure"] = {
        "provider": provider,
        "code": code,
        "query_preview": query[:120],
        "ts": int(time.time()),
    }
