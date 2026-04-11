"""
Intent Router Engine — The Brain.

Flow:
  1. Retrieve relevant memories (hash + FTS dual-layer).
  2. Check task complexity → single agent or multi-agent crew.
  3. Generate response (single LLM or crew discussion).
  4. Sanitize output (strip leaked secrets / prompt fragments).
  5. Process turn for memory extraction in background.
"""

from __future__ import annotations

import json
import logging
import time
import yaml
from pathlib import Path
from typing import Any

from my_agent_os.agent_core.llm_client import call_llm
from my_agent_os.auth.sanitizer import sanitize_output
from my_agent_os.skills_layer.base import normalize_skill_result
from my_agent_os.skills_layer.tools import get_tool, list_tools

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_memory_engine = None
_crew_orchestrator = None
_PROMPTS_CACHE: dict | None = None

CREW_COMPLEXITY_THRESHOLD = 0.6  # Only trigger crew for genuinely complex tasks (score 4+)


def set_memory_engine(engine) -> None:
    global _memory_engine
    _memory_engine = engine


def set_crew_orchestrator(orchestrator) -> None:
    global _crew_orchestrator
    _crew_orchestrator = orchestrator


def _load_prompts(filename: str = "system_prompts.yaml") -> dict:
    global _PROMPTS_CACHE
    if _PROMPTS_CACHE is None:
        with open(_PROMPTS_DIR / filename, "r", encoding="utf-8") as f:
            _PROMPTS_CACHE = yaml.safe_load(f)
    return _PROMPTS_CACHE


def _build_system_message(prompts: dict, channel: str) -> str:
    parts = [
        prompts["core_identity"],
        prompts["control_aesthetic"],
    ]
    if prompts.get("memory_grounding"):
        parts.append(prompts["memory_grounding"])
    parts.append(prompts["decision_engine"])
    channel_key = f"channel_{channel}"
    if channel_key in prompts:
        parts.append(prompts[channel_key])
    elif channel == "whatsapp" and "channel_mobile" in prompts:
        parts.append(prompts["channel_mobile"])
    return "\n".join(parts)


async def route(
    raw_input: str,
    channel: str,
    user_id: str,
    with_memory: bool = True,
    force_crew: bool = False,
) -> dict[str, Any]:
    start_ts = time.perf_counter()
    # user_id already carries the channel prefix (e.g. "whatsapp:+86xxx")
    # Use it directly as session_id to avoid double-prefix in audit logs.
    session_id = user_id

    prompts = _load_prompts()
    system_msg = _build_system_message(prompts, channel)
    preferences = prompts.get("preferences", {})

    user_payload_parts = [raw_input]

    if with_memory and _memory_engine:
        try:
            ctx = await _memory_engine.retrieve(user_id, raw_input)
            if ctx.summary_layer:
                memory_block = "\n\n[Retrieved Memories]\n" + ctx.summary_layer
                if ctx.decision_layer:
                    memory_block += "\n\n[Key Decisions]\n" + ctx.decision_layer
                user_payload_parts.append(memory_block)
        except Exception as e:
            logger.warning("Memory retrieval failed (non-fatal): %s", e)

    user_payload_parts.append(
        "\n[Soft defaults — lifestyle/aesthetic only; do not substitute for missing business facts]\n"
        + json.dumps(preferences, ensure_ascii=False)
    )

    # Complexity routing: crew (console only) or single agent
    if _crew_orchestrator and channel == "console":
        if force_crew:
            return await _route_via_crew(raw_input, user_id, system_msg, user_payload_parts, channel, start_ts)
        complexity = await _check_complexity(raw_input)
        if complexity >= CREW_COMPLEXITY_THRESHOLD:
            logger.info("Crew routing triggered: complexity=%.2f input=%.60s", complexity, raw_input)
            return await _route_via_crew(raw_input, user_id, system_msg, user_payload_parts, channel, start_ts)

    # Skill dispatch: check if the input maps to a registered skill
    skill_result = await _try_skill_dispatch(raw_input, user_payload_parts)
    if skill_result is None and await _detect_skill_gap(raw_input):
        logger.info("SKILL_GAP detected: %.80s", raw_input)
    if skill_result is not None:
        if _memory_engine:
            _memory_engine.process_turn_background(user_id, raw_input, skill_result.get("answer", ""))
        latency_ms = (time.perf_counter() - start_ts) * 1000
        try:
            from my_agent_os.enterprise.audit import log_route
            log_route(session_id=session_id, channel=channel, user_id=user_id,
                      raw_input=raw_input, response=skill_result, latency_ms=round(latency_ms, 2))
        except Exception:
            pass
        return skill_result

    # Single-agent path (whatsapp, mobile, console fallback)
    raw_response = await call_llm(
        system_message=system_msg,
        user_message="\n".join(user_payload_parts),
    )
    parsed = _parse_response(raw_response, channel)
    parsed = _sanitize_parsed(parsed, channel)

    if _memory_engine:
        answer = parsed.get("answer") or parsed.get("brief") or ""
        _memory_engine.process_turn_background(user_id, raw_input, answer)

    latency_ms = (time.perf_counter() - start_ts) * 1000
    try:
        from my_agent_os.enterprise.audit import log_route

        log_route(
            session_id=session_id,
            channel=channel,
            user_id=user_id,
            raw_input=raw_input,
            response=parsed,
            latency_ms=round(latency_ms, 2),
        )
    except Exception as e:
        logger.debug("Audit log skip: %s", e)

    return parsed


async def _check_complexity(raw_input: str) -> float:
    from my_agent_os.agent_core.crew.protocols import confidence_check
    try:
        return await confidence_check(call_llm, raw_input)
    except Exception as e:
        logger.warning("Complexity check failed: %s", e)
        return 0.0


async def _route_via_crew(
    raw_input: str,
    user_id: str,
    system_msg: str,
    user_payload_parts: list[str],
    channel: str,
    start_ts: float | None = None,
) -> dict[str, Any]:
    """Run multi-agent crew discussion and return structured result.

    The crew receives only the user's raw question as its task — NOT the full
    memory payload.  Memory context is useful for the single-agent path (which
    the LLM reads as part of the user message) but pollutes crew discussions
    by making every department apply unrelated historical context to the task.
    The Chief of Staff synthesises a single answer the user will see directly.
    """
    try:
        result = await _crew_orchestrator.discuss(
            task=raw_input,   # raw question only — no memory noise
        )
        parsed = {
            "answer": result.recommendation,
            "sources": None,
            "next_actions": [],
            "crew_views": result.department_views,
        }
    except Exception as e:
        logger.error("Crew discussion failed, falling back to single agent: %s", e)
        raw_response = await call_llm(
            system_message=system_msg,
            user_message="\n".join(user_payload_parts),
        )
        parsed = _parse_response(raw_response, channel)

    parsed = _sanitize_parsed(parsed, channel)

    if _memory_engine:
        answer = parsed.get("answer") or ""
        _memory_engine.process_turn_background(user_id, raw_input, answer)

    if start_ts is not None:
        latency_ms = (time.perf_counter() - start_ts) * 1000
        try:
            from my_agent_os.enterprise.audit import log_route

            log_route(
                session_id=f"{channel}:{user_id}",
                channel=channel,
                user_id=user_id,
                raw_input=raw_input,
                response=parsed,
                latency_ms=round(latency_ms, 2),
            )
        except Exception as e:
            logger.debug("Audit log skip: %s", e)

    return parsed


def _sanitize_parsed(parsed: dict[str, Any], channel: str) -> dict[str, Any]:
    """Apply output sanitizer to all user-facing text fields."""
    if "answer" in parsed and parsed["answer"]:
        parsed["answer"] = sanitize_output(parsed["answer"])
    if "brief" in parsed and parsed["brief"]:
        parsed["brief"] = sanitize_output(parsed["brief"])
    if "crew_views" in parsed and parsed["crew_views"]:
        parsed["crew_views"] = {
            k: sanitize_output(v) for k, v in parsed["crew_views"].items()
        }
    return parsed


def _parse_response(raw: str, channel: str) -> dict[str, Any]:
    data = _try_extract_json(raw)

    if data is None:
        if channel == "mobile":
            return {"action": "respond", "options": None, "brief": raw.strip()[:200]}
        return {"answer": raw.strip(), "sources": None, "next_actions": []}

    actions = (
        data.get("next_actions")
        or data.get("prioritized_next_actions")
        or data.get("actions")
        or []
    )
    answer = str(
        data.get("answer")
        or data.get("response")
        or data.get("brief")
        or data.get("message")
        or ""
    )

    if channel == "mobile":
        options = data.get("options")
        return {
            "action": data.get("action", "respond"),
            "options": _normalize_str_list(options) if options else None,
            "brief": (answer or raw.strip())[:200],
        }
    raw_sources = data.get("sources") or data.get("memory_sources")
    sources = None
    if isinstance(raw_sources, list) and raw_sources:
        dict_only = [x for x in raw_sources if isinstance(x, dict)]
        sources = dict_only or None

    return {
        "answer": answer,
        "sources": sources,
        "next_actions": _normalize_str_list(actions),
    }


def _try_extract_json(raw: str) -> dict[str, Any] | None:
    cleaned = raw.strip()

    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    start = cleaned.find("{")
    if start != -1:
        depth, end = 0, start
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            return json.loads(cleaned[start:end])
        except (json.JSONDecodeError, ValueError):
            pass

    return None


async def _try_skill_dispatch(
    raw_input: str,
    user_payload_parts: list[str],
) -> dict[str, Any] | None:
    """
    Ask the LLM to classify whether the user's request maps to a registered skill.
    Returns a formatted response dict if a skill executes successfully, else None.
    """
    tools = list_tools()
    if not tools:
        return None
    tool_names = {t["name"] for t in tools}

    direct = _try_direct_skill_route(raw_input, tool_names)
    if direct:
        skill_name, params = direct
        try:
            tool = get_tool(skill_name)
            raw_result = await tool.execute(params)
            skill = normalize_skill_result(raw_result if isinstance(raw_result, dict) else {"success": False, "reason": "Skill returned non-dict payload"})
            output = skill.get("output") or skill.get("message") or ""
            if not skill.get("ok", False):
                output = _format_skill_error_for_user(skill_name, skill)
            return {
                "answer": output,
                "sources": None,
                "next_actions": _skill_next_actions(skill),
                "skill_used": skill_name,
                "skill_ok": bool(skill.get("ok", False)),
                "skill_code": skill.get("code"),
                "skill_provider": skill.get("provider"),
                "route_path": "skill:direct",
            }
        except Exception as e:
            logger.warning("Direct skill route failed [%s]: %s", skill_name, e)

    tool_list = "\n".join(f'  "{t["name"]}": {t["description"]}' for t in tools)
    classifier_system = (
        "You are a skill dispatcher. Given a user message, decide if it maps to one of these tools:\n"
        + tool_list
        + "\n\nRespond ONLY with a JSON object: "
        '{\"skill\": \"<name or null>\", \"params\": {<extracted params>}}\n'
        "If no skill matches, return {\"skill\": null}. Never add explanation."
    )

    skill_name: str | None = None
    try:
        raw = await call_llm(
            system_message=classifier_system,
            user_message=raw_input,
            response_json=True,
            temperature=0.1,
        )
        data = _try_extract_json(raw)
        if not data or not data.get("skill"):
            return None

        skill_name = data["skill"]
        params     = data.get("params", {})

        try:
            tool = get_tool(skill_name)
            raw_result = await tool.execute(params)
            skill = normalize_skill_result(raw_result if isinstance(raw_result, dict) else {"success": False, "reason": "Skill returned non-dict payload"})
        except KeyError:
            return None

        output = skill.get("output") or skill.get("message") or ""
        if not skill.get("ok", False):
            output = _format_skill_error_for_user(skill_name, skill)

        return {
            "answer": output,
            "sources": None,
            "next_actions": _skill_next_actions(skill),
            "skill_used": skill_name,
            "skill_ok": bool(skill.get("ok", False)),
            "skill_code": skill.get("code"),
            "skill_provider": skill.get("provider"),
            "route_path": "skill:llm-dispatch",
        }
    except KeyError:
        return None
    except Exception as e:
        if skill_name:
            logger.warning("Skill execution error [%s]: %s", skill_name, e)
            return {
                "answer":       f"I found the right skill ({skill_name}) but ran into an error: {e}",
                "sources":      None,
                "next_actions": ["Try rephrasing", "Check skill configuration"],
                "skill_used":   skill_name,
                "skill_error":  True,
            }
        logger.debug("Skill dispatch classification failed (non-fatal): %s", e)
        return None


_ACTION_KEYWORDS = {
    "search", "find", "get", "fetch", "create", "generate", "send", "write",
    "calculate", "convert", "check", "download", "weather", "remind", "email",
    "搜索", "查找", "生成", "创建", "发送", "计算", "转换", "查天气", "提醒",
}


async def _detect_skill_gap(raw_input: str) -> bool:
    """Return True if the input looks like it needs a tool but none matched."""
    lowered = raw_input.lower()
    return any(kw in lowered for kw in _ACTION_KEYWORDS)


def _normalize_str_list(items: Any) -> list[str]:
    if not isinstance(items, list):
        return [str(items)] if items else []
    result = []
    for item in items:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            result.append(
                item.get("action") or item.get("description") or str(item)
            )
        else:
            result.append(str(item))
    return result


def _try_direct_skill_route(raw_input: str, tool_names: set[str]) -> tuple[str, dict[str, Any]] | None:
    text = (raw_input or "").strip()
    low = text.lower()

    # Deterministic routing for web search avoids LLM misclassification drift.
    if "web_search" in tool_names:
        markers = (
            "上网搜索",
            "帮我搜索",
            "请搜索",
            "查一下",
            "查一查",
            "搜一下",
            "搜一搜",
            "搜索",
            "search ",
            "search:",
            "look up",
            "google",
        )
        if any(m in low for m in markers):
            query = text
            replacements = [
                "上网搜索",
                "帮我搜索",
                "请搜索",
                "查一下",
                "查一查",
                "搜一下",
                "搜一搜",
                "搜索",
                "search:",
                "search ",
                "look up",
                "google",
            ]
            for rep in replacements:
                query = query.replace(rep, " ")
            query = query.strip(" ：:，,。.!?！？")
            if not query:
                query = text
            return ("web_search", {"query": query, "num_results": 5})
    return None


def _format_skill_error_for_user(skill_name: str, skill: dict[str, Any]) -> str:
    code = str(skill.get("code", "SKILL_FAILED"))
    message = str(skill.get("message", "Skill execution failed."))
    provider = skill.get("provider")
    provider_hint = f" Provider: {provider}." if provider else ""
    if code == "EMPTY_RESULT":
        return f"[{skill_name}] No external results were found.{provider_hint}"
    if code == "PROVIDER_TIMEOUT":
        return f"[{skill_name}] Search provider timed out.{provider_hint} Please retry."
    if code in ("PROVIDER_RATE_LIMIT", "RATE_LIMIT"):
        return f"[{skill_name}] Rate limit reached.{provider_hint} Wait and retry."
    if code == "NO_PROVIDER":
        return f"[{skill_name}] Search provider is not configured.{provider_hint} Configure API keys in .env."
    if code == "AUTH_ERROR":
        return f"[{skill_name}] Provider authentication failed.{provider_hint} Check API key."
    if code == "PROVIDER_ERROR":
        return f"[{skill_name}] Provider request failed.{provider_hint} {message}"
    return f"[{skill_name}] failed: {message}"


def _skill_next_actions(skill: dict[str, Any]) -> list[str]:
    if skill.get("ok", False):
        return []
    code = str(skill.get("code", "SKILL_FAILED"))
    if code in ("PROVIDER_TIMEOUT", "PROVIDER_RATE_LIMIT", "RATE_LIMIT"):
        return ["Retry in a moment", "Use a narrower query"]
    if code == "NO_PROVIDER":
        return ["Configure search API key", "Retry after deploy"]
    if code == "AUTH_ERROR":
        return ["Check provider API key", "Retry request"]
    if code == "EMPTY_RESULT":
        return ["Try broader keywords", "Try English + Chinese keywords"]
    return ["Try rephrasing", "Check skill configuration"]
