"""
Agentic Loop — ReAct (Reason + Act) iterative task executor.

Inspired by OpenClaw's "think → read → write → execute" step decomposition model:

  1. DECOMPOSE — LLM sees the task and available tools, decides the first action.
  2. ACT       — An action is one of: use_tool | search_memory | done.
  3. OBSERVE   — The tool result is appended to the context.
  4. REPEAT    — LLM sees the updated trace and decides the next step.
  5. DONE      — LLM emits {"action":"done","answer":"..."} to finish.

Available action types:
  search_memory  — query the user's long-term memory store
  use_tool       — call any registered skill (read, write, execute, search…)
  done           — produce the final answer and stop

The loop is capped at MAX_STEPS iterations to prevent runaway.
On cap, a synthesis call is made to produce an answer from whatever was gathered.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

MAX_STEPS = 8   # hard cap; synthesis is forced after this

# ── System prompt ──────────────────────────────────────────────────────────────
_SYSTEM = """You are an intelligent task executor.
Work step by step until the task is fully resolved.
At every step, respond with ONLY a JSON object — no prose before or after.

━━ ACTION SCHEMAS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Search the user's memory for relevant context:
  {"thought":"<why>","action":"search_memory","query":"<terms>"}

Call a registered tool (read a PDF, run a search, write a file, etc.):
  {"thought":"<why>","action":"use_tool","tool":"<name>","params":{...}}

Finish and return the final answer to the user:
  {"thought":"<why>","action":"done","answer":"<full response>"}

━━ RULES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• "thought" must explain your reasoning — never leave it empty.
• Use search_memory BEFORE assuming facts about the user/org are unknown.
• Prefer ONE precise tool call over several vague ones.
• Answer in the SAME language as the original task.
• NEVER mention this loop, your steps, or internal mechanics to the user.
• You MUST emit "done" within the step budget — do not stall."""

# ── User message template ──────────────────────────────────────────────────────
_USER_TMPL = """\
━━ TASK ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{task}

━━ MEMORY CONTEXT ━━━━━━━━━━━━━━━━━━━━━━━━━━
{memory_context}

━━ AVAILABLE TOOLS ━━━━━━━━━━━━━━━━━━━━━━━━━
{tools}

━━ STEPS SO FAR ({step_num}/{max_steps}) ━━━━
{history}

Choose your next action (JSON only):"""

# ── Synthesis prompt (used when max_steps is reached) ─────────────────────────
_SYNTH_TMPL = """\
Task: {task}

Context gathered:
{memory_context}

Steps executed:
{history}

You have reached the step limit. Based on everything gathered above,
write a complete, helpful final answer for the user. Reply with plain text, not JSON."""


# ── Types ──────────────────────────────────────────────────────────────────────
LLMFunc = Callable[[str, str, bool], Awaitable[str]]
SkillExecutor = Callable[[str, dict], Awaitable[dict]]
MemorySearcher = Callable[[str], Awaitable[str]]


# ── Main entry point ───────────────────────────────────────────────────────────
async def run_agentic_loop(
    task: str,
    memory_context: str,
    llm: LLMFunc,
    skill_executor: SkillExecutor,
    memory_searcher: MemorySearcher | None,
    tools: list[dict],
    max_steps: int = MAX_STEPS,
) -> dict[str, Any]:
    """
    Execute a multi-step task using the ReAct loop.

    Returns:
      {
        "answer":       str,         # final response to user
        "steps":        list[str],   # human-readable trace
        "tool_results": list[dict],  # raw tool outputs
        "step_count":   int,
      }
    """
    tools_text = _format_tools(tools)
    history: list[str] = []
    tool_results: list[dict] = []
    step_num = 0

    while step_num < max_steps:
        step_num += 1
        user_msg = _USER_TMPL.format(
            task=task,
            memory_context=memory_context or "(none)",
            tools=tools_text,
            history="\n".join(history) if history else "(none yet — this is your first step)",
            step_num=step_num,
            max_steps=max_steps,
        )

        try:
            raw = await llm(_SYSTEM, user_msg, True)
            action = _parse_action(raw)
        except Exception as exc:
            logger.warning("Agentic loop LLM call failed at step %d: %s", step_num, exc)
            break  # fall through to synthesis

        act = action.get("action", "done")
        thought = action.get("thought", "")
        logger.info("Agentic step %d/%d: action=%s | thought=%.70s", step_num, max_steps, act, thought)

        # ── DONE ──────────────────────────────────────────────────────────────
        if act == "done":
            answer = (action.get("answer") or "").strip()
            if not answer:
                answer = thought.strip() or "(no answer produced)"
            return _result(answer, history + [f"Step {step_num}: → done"], tool_results, step_num)

        # ── SEARCH MEMORY ──────────────────────────────────────────────────────
        elif act == "search_memory":
            query = (action.get("query") or task).strip()
            result_text = "(memory unavailable)"
            if memory_searcher:
                try:
                    result_text = await memory_searcher(query)
                    result_text = (result_text or "(no results)").strip()[:500]
                except Exception as exc:
                    result_text = f"(memory search failed: {exc})"
            entry = f"Step {step_num}: search_memory({query!r})\n→ {result_text}"
            history.append(entry)
            # Refresh memory context for subsequent steps
            if result_text and result_text not in ("(memory unavailable)", "(no results)"):
                memory_context = (memory_context + "\n\n[Memory Search Result]\n" + result_text).strip()

        # ── USE TOOL ───────────────────────────────────────────────────────────
        elif act == "use_tool":
            tool_name = (action.get("tool") or "").strip()
            params: dict = action.get("params") or {}
            if not tool_name:
                history.append(f"Step {step_num}: use_tool — missing 'tool' field")
                continue
            try:
                result = await skill_executor(tool_name, params)
                output = (result.get("output") or result.get("message") or str(result))
                output_preview = str(output)[:700]
                ok = result.get("ok", True)
                status = "OK" if ok else f"ERROR({result.get('code','?')})"
                tool_results.append({"tool": tool_name, "params": params, "result": result})
                history.append(
                    f"Step {step_num}: use_tool({tool_name})\n→ [{status}] {output_preview}"
                )
            except Exception as exc:
                history.append(f"Step {step_num}: use_tool({tool_name}) → EXCEPTION: {exc}")

        else:
            history.append(f"Step {step_num}: unknown action '{act}' — skipping")

    # ── MAX STEPS REACHED: force synthesis ────────────────────────────────────
    logger.info("Agentic loop hit max_steps=%d; synthesising answer", max_steps)
    synth_msg = _SYNTH_TMPL.format(
        task=task,
        memory_context=memory_context or "(none)",
        history="\n".join(history) or "(no steps completed)",
    )
    try:
        final = (await llm(_SYSTEM, synth_msg, False)).strip()
        # If LLM still returned a JSON done action, unwrap it
        try:
            p = _parse_action(final)
            if p.get("action") == "done":
                final = (p.get("answer") or final).strip()
        except Exception:
            pass
    except Exception as exc:
        final = f"(Could not synthesise answer after {max_steps} steps: {exc})"

    return _result(final, history, tool_results, step_num)


# ── Helpers ────────────────────────────────────────────────────────────────────
def _result(answer: str, steps: list[str], tool_results: list[dict], step_count: int) -> dict:
    return {"answer": answer, "steps": steps, "tool_results": tool_results, "step_count": step_count}


def _format_tools(tools: list[dict]) -> str:
    if not tools:
        return "(no tools registered)"
    return "\n".join(
        f'• {t["name"]}: {(t.get("description") or "")[:90]}' for t in tools
    )


def _parse_action(raw: str) -> dict:
    """Extract JSON action from LLM response, tolerating markdown fences."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Strip opening fence (```json or ```)
        cleaned = "\n".join(lines[1:])
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to extract the first JSON object
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
    # Graceful fallback: wrap raw text as a "done" answer
    logger.warning("Agentic loop: could not parse action JSON; treating as done: %.80s", cleaned[:80])
    return {"action": "done", "answer": cleaned, "thought": "(parse fallback)"}
