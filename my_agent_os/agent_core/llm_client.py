"""
LLM Client — 多模型抽象层（OpenClaw Agent Runner 风格）。

支持的 Provider:
  deepseek   — DeepSeek API（OpenAI 兼容端点，默认）
  openai     — OpenAI GPT 系列
  anthropic  — Anthropic Claude 系列
  gemini     — Google Gemini 系列
  ollama     — 本地 Ollama（OpenAI 兼容）

Provider 由 settings.LLM_PROVIDER 或 ~/.coreclaw/config.json 中的
llm_provider 字段控制，可在运行时动态切换，无需重启。

设计原则（Control Aesthetic）：
  - 调用失败 → 优雅降级，返回平静的兜底消息，绝不向用户暴露裸错误。
  - 指数退避重试（最多 3 次），带 jitter 防惊群。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

import httpx

from my_agent_os.config.settings import settings

logger = logging.getLogger(__name__)

# OpenClaw-style retry
LLM_RETRY_ATTEMPTS = 3
LLM_RETRY_MIN_MS = 400
LLM_RETRY_MAX_MS = 30000
LLM_RETRY_JITTER = 0.1

# 各 provider 默认模型
_PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-haiku-20240307",
    "gemini": "gemini-1.5-flash",
    "ollama": "llama3",
}


# ── 公开入口 ──────────────────────────────────────────────────────────────────


async def call_llm(
    system_message: str,
    user_message: str,
    response_json: bool = False,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """
    统一 LLM 调用入口。
    根据 settings.LLM_PROVIDER 自动路由到对应 provider；
    优先使用 ~/.coreclaw/config.json 中的 llm_provider 覆盖。
    失败后优雅降级，永不抛出异常。
    """
    provider = _resolve_provider()
    last_err: Exception | None = None

    for attempt in range(LLM_RETRY_ATTEMPTS):
        try:
            return await _dispatch(
                provider, system_message, user_message, response_json, temperature, max_tokens
            )
        except Exception as exc:
            last_err = exc
            logger.warning(
                "[%s] attempt %d/%d failed: %s",
                provider, attempt + 1, LLM_RETRY_ATTEMPTS, exc,
            )
            if attempt < LLM_RETRY_ATTEMPTS - 1:
                delay_ms = min(
                    LLM_RETRY_MAX_MS,
                    LLM_RETRY_MIN_MS
                    * (2**attempt)
                    * (1 + random.uniform(-LLM_RETRY_JITTER, LLM_RETRY_JITTER)),
                )
                await asyncio.sleep(delay_ms / 1000)

    logger.error("[%s] 调用失败（所有重试耗尽）: %s", provider, last_err)
    return _graceful_fallback()


def get_active_provider() -> str:
    """返回当前激活的 LLM provider 名称。"""
    return _resolve_provider()


# ── Provider 路由 ─────────────────────────────────────────────────────────────


def _resolve_provider() -> str:
    """优先读 ~/.coreclaw/config.json，其次 settings.LLM_PROVIDER。"""
    try:
        from my_agent_os.config.local_config import get_local_config
        p = get_local_config().llm_provider
        if p:
            return p
    except Exception:
        pass
    return getattr(settings, "LLM_PROVIDER", "deepseek") or "deepseek"


def _resolve_model(provider: str) -> str:
    """优先读 ~/.coreclaw/config.json，其次 settings.LLM_MODEL，最后 provider 默认值。"""
    try:
        from my_agent_os.config.local_config import get_local_config
        m = get_local_config().llm_model
        if m:
            return m
    except Exception:
        pass
    m = getattr(settings, "LLM_MODEL", "") or ""
    if not m and provider == "deepseek":
        m = getattr(settings, "DEEPSEEK_MODEL", "") or ""
    return m or _PROVIDER_DEFAULT_MODELS.get(provider, "gpt-4o-mini")


async def _dispatch(
    provider: str,
    system_message: str,
    user_message: str,
    response_json: bool,
    temperature: float,
    max_tokens: int,
) -> str:
    if provider in ("deepseek", "openai", "ollama"):
        return await _call_openai_compat(
            provider, system_message, user_message, response_json, temperature, max_tokens
        )
    if provider == "anthropic":
        return await _call_anthropic(
            system_message, user_message, response_json, temperature, max_tokens
        )
    if provider == "gemini":
        return await _call_gemini(
            system_message, user_message, response_json, temperature, max_tokens
        )
    raise ValueError(f"未知 LLM provider: {provider!r}")


# ── OpenAI 兼容端点（DeepSeek / OpenAI / Ollama）────────────────────────────


async def _call_openai_compat(
    provider: str,
    system_message: str,
    user_message: str,
    response_json: bool,
    temperature: float,
    max_tokens: int,
) -> str:
    if provider == "deepseek":
        base_url = getattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        api_key = getattr(settings, "DEEPSEEK_API_KEY", "")
    elif provider == "openai":
        base_url = getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
        api_key = getattr(settings, "OPENAI_API_KEY", "")
        # 去除末尾 /v1 避免双重追加
        base_url = base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
    else:  # ollama
        base_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
        api_key = "ollama"  # Ollama 不校验 key

    model = _resolve_model(provider)
    url = f"{base_url.rstrip('/')}/v1/chat/completions"

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_json:
        payload["response_format"] = {"type": "json_object"}

    return await _http_post(
        url,
        payload,
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )


# ── Anthropic ─────────────────────────────────────────────────────────────────


async def _call_anthropic(
    system_message: str,
    user_message: str,
    response_json: bool,
    temperature: float,
    max_tokens: int,
) -> str:
    base_url = getattr(settings, "ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    api_key = getattr(settings, "ANTHROPIC_API_KEY", "")
    model = _resolve_model("anthropic")
    url = f"{base_url.rstrip('/')}/v1/messages"

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_message,
        "messages": [{"role": "user", "content": user_message}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    proxy = getattr(settings, "HTTPS_PROXY", "") or None
    timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            logger.error("Anthropic HTTP %s — %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"]


# ── Gemini ────────────────────────────────────────────────────────────────────


async def _call_gemini(
    system_message: str,
    user_message: str,
    response_json: bool,
    temperature: float,
    max_tokens: int,
) -> str:
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    model = _resolve_model("gemini")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    combined = f"{system_message}\n\n{user_message}"
    payload: dict[str, Any] = {
        "contents": [{"parts": [{"text": combined}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if response_json:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    proxy = getattr(settings, "HTTPS_PROXY", "") or None
    timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            logger.error("Gemini HTTP %s — %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


# ── 公共 HTTP 工具 ────────────────────────────────────────────────────────────


async def _http_post(url: str, payload: dict, headers: dict) -> str:
    proxy = getattr(settings, "HTTPS_PROXY", "") or None
    timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            logger.error("LLM HTTP %s — %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


# ── 优雅降级 ──────────────────────────────────────────────────────────────────


def _graceful_fallback() -> str:
    """
    Control Aesthetic: 即便全部失败，也返回平静的消息，绝不向用户暴露错误堆栈。
    同时包含 console + mobile schema，确保下游解析器始终收到有效字段。
    """
    return json.dumps(
        {
            "answer": "暂时无法处理请求，已记录，稍后重试。",
            "next_actions": ["片刻后重试"],
            "action": "respond",
            "options": ["重试", "取消"],
            "brief": "暂时无法处理请求，请稍后重试。",
        }
    )
