"""
Image Analyzer — describe or analyze image content via vision LLM.

Accepts a local file path (PNG/JPG/WEBP/GIF) or a base64 data URI.
Encodes the image to base64 and calls the LLM with the multimodal message format.

Params:
  path    (str) — local file path OR data URI (data:image/…;base64,…)
  prompt  (str) — what to analyze (default: "Describe this image in detail.")
  model   (str) — vision model override (default: VISION_MODEL env or deepseek-vl2)

Requires: a vision-capable LLM endpoint (DeepSeek VL2 / OpenAI GPT-4o etc.)
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from my_agent_os.config.settings import settings
from my_agent_os.skills_layer.base import Skill, skill_err, skill_ok
from my_agent_os.skills_layer.tools import register

_WORKSPACE = Path(os.getenv("AGENT_WORKSPACE_DIR", Path.home() / "AgentOS" / "workspace"))
_MAX_BYTES = 20 * 1024 * 1024  # 20 MB

_MIME_MAP: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    for candidate in [(_WORKSPACE / path_str).resolve(), Path.cwd() / path_str]:
        if candidate.exists():
            return candidate
    return p


def _load_file(path: Path) -> tuple[str, str]:
    """Return (mime, base64_str)."""
    mime = _MIME_MAP.get(path.suffix.lower(), "image/jpeg")
    data = path.read_bytes()
    if len(data) > _MAX_BYTES:
        raise ValueError(f"Image is {len(data) // 1_000_000} MB; max is 20 MB.")
    return mime, base64.b64encode(data).decode()


@register
class ImageAnalyzer(Skill):
    name = "image_analyzer"
    description = (
        "Analyze or describe the content of an image using a vision LLM. "
        "Params: path (str, file path or data URI, required), "
        "prompt (str, analysis instruction, optional), "
        "model (str, vision model override, optional). "
        "Requires a vision-capable model (VISION_MODEL env, default deepseek-vl2)."
    )

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        path_str = (params.get("path") or "").strip()
        if not path_str:
            return skill_err("MISSING_PARAM", "Parameter 'path' is required.")

        prompt = (params.get("prompt") or "Describe this image in detail.").strip()
        model = (
            (params.get("model") or "").strip()
            or os.getenv("VISION_MODEL", "")
            or "deepseek-vl2"
        )

        # Detect base64 data URI
        if path_str.startswith("data:image/"):
            try:
                mime = path_str.split(";")[0].split(":")[1]
                b64 = path_str.split(",", 1)[1]
            except (IndexError, ValueError):
                return skill_err("INVALID_DATA_URI", "Could not parse base64 data URI.")
        else:
            file_path = _resolve(path_str)
            if not file_path.exists():
                return skill_err("FILE_NOT_FOUND", f"Image not found: {path_str}")
            if file_path.suffix.lower() not in _MIME_MAP:
                return skill_err(
                    "UNSUPPORTED_FORMAT",
                    f"Unsupported format '{file_path.suffix}'. Supported: {sorted(_MIME_MAP)}",
                )
            try:
                mime, b64 = _load_file(file_path)
            except Exception as exc:
                return skill_err("ENCODE_FAILED", str(exc))

        try:
            description = await _call_vision(mime, b64, prompt, model)
            return skill_ok(description, output=description, data={"model": model})
        except Exception as exc:
            return skill_err("VISION_FAILED", f"Vision LLM call failed: {exc}")


async def _call_vision(mime: str, b64: str, prompt: str, model: str) -> str:
    api_key = (settings.DEEPSEEK_API_KEY or os.getenv("OPENAI_API_KEY", "")).strip()
    if not api_key:
        raise ValueError("No API key configured. Set DEEPSEEK_API_KEY or OPENAI_API_KEY.")
    base_url = (settings.DEEPSEEK_BASE_URL or "https://api.deepseek.com").rstrip("/")

    payload = json.dumps({
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 1024,
    }).encode()

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode())
    return result["choices"][0]["message"]["content"]
