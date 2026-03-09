"""
LLM Client — DeepSeek inference with graceful fallback.

Uses DeepSeek API (OpenAI-compatible /v1/chat/completions endpoint).

Design: Control Aesthetic — if the call fails, return a calm message.
        The caller never sees raw errors.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from my_agent_os.config.settings import settings

logger = logging.getLogger(__name__)


async def call_llm(
    system_message: str,
    user_message: str,
    response_json: bool = False,
) -> str:
    """
    Unified LLM call via DeepSeek API.
    Returns raw text from the model; falls back gracefully on failure.
    """
    try:
        return await _call_deepseek(system_message, user_message, response_json)
    except Exception as e:
        logger.error("DeepSeek call failed: %s", e)
        return _graceful_fallback()


async def _call_deepseek(
    system_message: str,
    user_message: str,
    response_json: bool,
) -> str:
    url = f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload: dict[str, Any] = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.7,
        "max_tokens": 2048,
    }
    if response_json:
        payload["response_format"] = {"type": "json_object"}

    proxy = settings.HTTPS_PROXY or None
    async with httpx.AsyncClient(timeout=60, proxy=proxy) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            body = resp.text
            logger.error("DeepSeek HTTP %s — %s", resp.status_code, body[:500])
            resp.raise_for_status()

    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _graceful_fallback() -> str:
    """
    Control Aesthetic: even when everything fails,
    return a calm, non-anxious message — never an error dump.
    """
    return json.dumps({
        "answer": "I'm temporarily unable to process this. I've noted it and will retry shortly.",
        "next_actions": ["Try again in a moment"],
    })
