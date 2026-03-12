"""
Memory Feedback — Hook for user correction signals (future learning).

When user says "you forgot X" or corrects the agent, call record_memory_feedback().
Stored for future use: adaptive retrieval weights, importance prediction, etc.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_FEEDBACK_DIR = Path(__file__).parent.parent / "memory_layer" / "data" / "feedback"
_ENABLED = True


def _ensure_dir() -> Path:
    _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    return _FEEDBACK_DIR


def record_memory_feedback(
    *,
    user_id: str,
    query: str,
    source_ids: list[str],
    feedback_text: str,
    session_id: str | None = None,
) -> None:
    """
    Record that the user indicated missing/incorrect memory.
    Call when user says "you forgot X", "last time we said Y", etc.
    """
    if not _ENABLED:
        return
    try:
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "user_id": user_id,
            "query": query[:500],
            "source_ids": source_ids,
            "feedback_text": feedback_text[:500],
            "session_id": session_id,
        }
        path = _ensure_dir() / "memory_feedback.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("Feedback record failed: %s", e)


def enable(enabled: bool = True) -> None:
    global _ENABLED
    _ENABLED = enabled
