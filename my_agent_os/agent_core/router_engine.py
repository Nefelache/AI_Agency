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


def _build_openclaw_system_message(prompts: dict) -> str:
    """
    OpenClaw Control UI channel: use an OpenClaw-shaped prompt pack instead of the
    executive-assistant + hidden-internals stack (which makes the model sound dull
    and triggers generic refusals). Structure follows OpenClaw's documented sections,
    adapted for Agent OS (Python router, skills, memory engine)—not the Node gateway.
    """
    keys = (
        "openclaw_core",
        "openclaw_safety",
        "openclaw_memory_recall",
        "openclaw_skills",
        "openclaw_tool_style",
        "openclaw_workspace",
        "openclaw_docs",
        "channel_openclaw",
    )
    blocks: list[str] = []
    for k in keys:
        block = prompts.get(k)
        if isinstance(block, str) and block.strip():
            blocks.append(block.strip())
    return "\n\n".join(blocks)


def _skills_instruction_catalog() -> str:
    """Per-skill instructions for the main LLM (OpenClaw-style SKILL.md content in-code)."""
    tools = list_tools()
    lines: list[str] = ["## Skill reference (follow when invoking a skill)", ""]
    for t in tools:
        name = t.get("name", "?")
        desc = (t.get("description") or "").strip()
        instr = (t.get("skill_instructions") or "").strip()
        lines.append(f"### {name}")
        if desc:
            lines.append(desc)
        if instr:
            lines.append(instr)
        lines.append("")
    return "\n".join(lines).strip()


def _append_skills_to_system(system_message: str, prompts: dict) -> str:
    """Append global dispatch rules + full skill docs to system prompt (all channels)."""
    dispatch = prompts.get("skill_dispatch_instructions")
    if isinstance(dispatch, str) and dispatch.strip():
        system_message = system_message + "\n\n" + dispatch.strip()
    return system_message + "\n\n" + _skills_instruction_catalog()


def _extract_skill_call(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    sc = data.get("skill_call")
    if not isinstance(sc, dict):
        return None
    name = sc.get("name") or sc.get("skill")
    if not name or not isinstance(name, str):
        return None
    params = sc.get("params")
    if not isinstance(params, dict):
        params = {}
    return {"name": name.strip(), "params": params}


async def _execute_skill_call(skill_call: dict[str, Any]) -> str | None:
    name = skill_call["name"]
    params = skill_call["params"]
    try:
        tool = get_tool(name)
        result = await tool.execute(params)
    except KeyError:
        logger.info("Unknown skill name from LLM: %s", name)
        return None
    except Exception as e:
        logger.warning("Skill %s raised: %s", name, e)
        return None

    if not result.get("success", True):
        logger.info("Skill %s failed: %s", name, result.get("reason"))
        return None
    return (result.get("output") or str(result)).strip() or None


def _merge_skill_into_parsed(parsed: dict[str, Any], skill_output: str, channel: str) -> None:
    if channel == "mobile":
        base = (parsed.get("brief") or "").strip()
        merged = f"{base}\n\n{skill_output}".strip() if base else skill_output
        parsed["brief"] = merged[:800]
    else:
        base = (parsed.get("answer") or "").strip()
        parsed["answer"] = f"{base}\n\n{skill_output}".strip() if base else skill_output


async def _apply_skill_from_parsed(parsed: dict[str, Any], channel: str) -> dict[str, Any]:
    sc = parsed.pop("skill_call", None)
    if not sc:
        return parsed
    out = await _execute_skill_call(sc)
    if out:
        _merge_skill_into_parsed(parsed, out, channel)
        parsed["skill_used"] = sc["name"]
    return parsed


def _build_system_message(prompts: dict, channel: str) -> str:
    if channel == "openclaw":
        return _build_openclaw_system_message(prompts)
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
    system_msg = _append_skills_to_system(_build_system_message(prompts, channel), prompts)

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

    client_ctx = prompts.get("client_context", {})
    if isinstance(client_ctx, dict) and any(
        v not in (None, "", [], {}) for v in client_ctx.values()
    ):
        user_payload_parts.append(
            f"\n[Client context]\n{json.dumps(client_ctx, ensure_ascii=False)}"
        )

    # Complexity routing: crew (console only) or single agent
    if _crew_orchestrator and channel == "console":
        if force_crew:
            return await _route_via_crew(raw_input, user_id, system_msg, user_payload_parts, channel, start_ts)
        complexity = await _check_complexity(raw_input)
        if complexity >= CREW_COMPLEXITY_THRESHOLD:
            return await _route_via_crew(raw_input, user_id, system_msg, user_payload_parts, channel, start_ts)

    # Single-agent path: main LLM decides skill_call + answer in one pass (OpenClaw-style)
    raw_response = await call_llm(
        system_message=system_msg,
        user_message="\n".join(user_payload_parts),
    )
    parsed = _parse_response(raw_response, channel)
    parsed = await _apply_skill_from_parsed(parsed, channel)
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
        parsed = await _apply_skill_from_parsed(parsed, channel)

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
    skill_call = _extract_skill_call(data) if isinstance(data, dict) else None

    if data is None:
        if channel == "mobile":
            return {
                "action": "respond",
                "options": None,
                "brief": raw.strip()[:200],
                "skill_call": None,
            }
        return {
            "answer": raw.strip(),
            "sources": None,
            "next_actions": [],
            "skill_call": None,
        }

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
            "skill_call": skill_call,
        }
    raw_sources = data.get("sources") or data.get("memory_sources")
    sources = raw_sources if isinstance(raw_sources, list) else None

    return {
        "answer": answer,
        "sources": sources,
        "next_actions": _normalize_str_list(actions),
        "skill_call": skill_call,
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
