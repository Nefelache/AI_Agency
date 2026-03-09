"""
Crew Protocols — Complexity routing and consensus helpers.

confidence_check: single LLM call to rate task complexity (0-1).
Used by router_engine to decide single-agent vs crew path.
"""

from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

LLMFunc = Callable[[str, str, bool], Awaitable[str]]

_COMPLEXITY_PROMPT = """Rate the complexity of this user request on a scale of 1-5:

1 = Simple factual question, scheduling, or acknowledgment
2 = Moderate task requiring some reasoning
3 = Multi-faceted decision involving trade-offs
4 = Strategic decision requiring multiple perspectives
5 = High-stakes decision with significant consequences

User request: {task}

Respond with ONLY a JSON object: {{"score": <1-5>, "reason": "<brief>"}}"""


async def confidence_check(llm: LLMFunc, task: str) -> float:
    """
    Returns a 0-1 complexity score.
    Score >= 0.6 (i.e. rating 3+) triggers multi-agent crew.
    """
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
