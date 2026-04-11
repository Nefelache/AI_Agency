"""
Crew Protocols — Complexity routing and consensus helpers.

confidence_check: single LLM call to rate task complexity (0-1).
Used by router_engine to decide single-agent vs crew path.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

LLMFunc = Callable[[str, str, bool], Awaitable[str]]

# Short-circuit: these inputs are ALWAYS single-agent, never crew.
_TRIVIAL_RE = re.compile(
    r"^(hi|hello|hey|ok|okay|sure|thanks|谢谢|好的|收到|好|嗯|哦|ping|pong|测试|test|check|试试"
    r"|你好|早上好|晚上好|下午好|再见|bye|gg|lol|哈|哈哈|haha|嗯嗯)[^\u4e00-\u9fff\w]*$",
    re.IGNORECASE,
)

_CREW_TEST_RE = re.compile(r"^测试\s*crew|^test\s*crew|^crew\s*test", re.IGNORECASE)

_COMPLEXITY_PROMPT = """Rate the complexity of this user request on a scale of 1-5:

1 = Greeting, acknowledgment, one-word reply, test/ping/meta-command, or casual small talk
    Examples: "hi", "ok", "测试", "测试crew", "test crew", "hello", "谢谢", "ping"
2 = Simple factual question, lookup, or single-step task
3 = Multi-step task requiring some reasoning or planning
4 = Strategic decision requiring multiple perspectives and trade-offs
5 = High-stakes complex decision with significant long-term consequences

IMPORTANT:
- Any input that looks like a system test, greeting, or meta-command is ALWAYS score 1.
- Short inputs under 5 characters are almost always score 1.
- "测试crew", "test crew", or similar crew-testing phrases are score 1 (meta, not a real task).
- Only score 4+ for genuine business/strategic decisions that REQUIRE multiple expert viewpoints.

User request: {task}

Respond with ONLY a JSON object: {{"score": <1-5>, "reason": "<brief>"}}"""


def is_trivial(task: str) -> bool:
    """Returns True for greetings, tests, and meta-commands that should never go to crew."""
    stripped = task.strip()
    if len(stripped) <= 6:
        return True
    if _TRIVIAL_RE.match(stripped):
        return True
    if _CREW_TEST_RE.match(stripped):
        return True
    return False


async def confidence_check(llm: LLMFunc, task: str) -> float:
    """
    Returns a 0-1 complexity score.
    Score >= 0.6 (rating 4+) triggers multi-agent crew; casual chat stays single-agent.
    """
    if is_trivial(task):
        logger.info("Trivial input short-circuit — skipping crew: %.60s", task)
        return 0.0

    prompt = _COMPLEXITY_PROMPT.replace("{task}", task)
    try:
        raw = await llm("You are a task complexity evaluator.", prompt, True)
        data = _parse_json(raw)
        score = int(data.get("score", 1))
        normalized = max(0.0, min(1.0, (score - 1) / 4.0))
        logger.info("Complexity check: score=%d (%.2f) — %s", score, normalized, data.get("reason", ""))
        return normalized
    except Exception as e:
        logger.warning("Complexity check failed, defaulting to simple: %s", e)
        return 0.0


def _parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            return json.loads(cleaned[start : end + 1])
        raise
