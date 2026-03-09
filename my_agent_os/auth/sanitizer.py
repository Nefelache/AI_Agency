"""
Output Sanitizer — Prevent sensitive data from leaking to the user.

Inspired by OpenClaw's 91% prompt injection success rate and
7.1% of marketplace skills leaking API keys in plaintext.

Scans LLM output for:
  - API key patterns (sk-..., AIza..., Bearer tokens)
  - Environment variable leaks (VAR_NAME=value)
  - System prompt fragments (SOUL GENOME, decision_engine, etc.)
"""

from __future__ import annotations

import re

_KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{30,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_.-]{20,}"),
    re.compile(r"os-(?:owner|channel|guest)-[a-f0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"xoxb-[A-Za-z0-9-]+"),
]

_ENV_PATTERN = re.compile(
    r"(?:DEEPSEEK_API_KEY|API_KEY_OWNER|API_KEY_CHANNEL|API_KEY_GUEST|"
    r"GEMINI_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|SECRET)"
    r"\s*=\s*\S+",
    re.IGNORECASE,
)

_PROMPT_FRAGMENTS = [
    "SOUL GENOME",
    "decision_engine",
    "control_aesthetic",
    "core_identity",
    "channel_mobile",
    "channel_console",
    "INTERNAL DECISION FRAMEWORK",
    "Three-Dimensional Weighted Logic",
    "w_global",
    "w_preference",
    "w_state",
]

_FRAGMENT_PATTERN = re.compile(
    "|".join(re.escape(f) for f in _PROMPT_FRAGMENTS),
    re.IGNORECASE,
)


def sanitize_output(text: str) -> str:
    """Remove sensitive patterns from LLM output before returning to user."""
    if not text:
        return text

    for pat in _KEY_PATTERNS:
        text = pat.sub("[REDACTED]", text)

    text = _ENV_PATTERN.sub("[REDACTED]", text)
    text = _FRAGMENT_PATTERN.sub("[REDACTED]", text)

    return text
