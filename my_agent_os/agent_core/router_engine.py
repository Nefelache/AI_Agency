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
        prompts["decision_engine"],
    ]
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
        f"\n[User Preferences]\n{json.dumps(preferences, ensure_ascii=False)}"
    )

    # Complexity routing: crew (console only) or single agent
    if _crew_orchestrator and channel == "console":
        if force_crew:
            return await _route_via_crew(raw_input, user_id, system_msg, user_payload_parts, channel, start_ts)
        complexity = await _check_complexity(raw_input)
        if complexity >= CREW_COMPLEXITY_THRESHOLD:
            return await _route_via_crew(raw_input, user_id, system_msg, user_payload_parts, channel, start_ts)

    # Skill dispatch: check if the input maps to a registered skill
    skill_result = await _try_skill_dispatch(raw_input, user_payload_parts)
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
    """Run multi-agent crew discussion and return structured result."""
    try:
        result = await _crew_orchestrator.discuss(
            task="\n".join(user_payload_parts),
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
    sources = raw_sources if isinstance(raw_sources, list) else None

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

    tool_list = "\n".join(f'  "{t["name"]}": {t["description"]}' for t in tools)
    classifier_system = (
        "You are a skill dispatcher. Given a user message, decide if it maps to one of these tools:\n"
        + tool_list
        + "\n\nRespond ONLY with a JSON object: "
        '{\"skill\": \"<name or null>\", \"params\": {<extracted params>}}\n'
        "If no skill matches, return {\"skill\": null}. Never add explanation."
    )

    try:
        raw = await call_llm(
            system_message=classifier_system,
            user_message=raw_input,
            stream=False,
        )
        data = _try_extract_json(raw)
        if not data or not data.get("skill"):
            return None

        skill_name = data["skill"]
        params     = data.get("params", {})

        try:
            tool   = get_tool(skill_name)
            result = tool.execute(params)
        except KeyError:
            return None

        output = result.get("output") or str(result)
        if not result.get("success", True):
            reason = result.get("reason", "Unknown error")
            output = f"[{skill_name}] failed: {reason}"

        return {
            "answer":       output,
            "sources":      None,
            "next_actions": [],
            "skill_used":   skill_name,
        }
    except Exception as e:
        logger.debug("Skill dispatch probe failed (non-fatal): %s", e)
        return None


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
