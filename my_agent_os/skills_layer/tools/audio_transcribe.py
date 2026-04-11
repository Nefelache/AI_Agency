"""
Audio Transcribe — convert speech in audio files to text via Whisper.

Accepts local file paths (MP3 / WAV / M4A / WEBM / OGG / FLAC / MPEG).
Uses the OpenAI Whisper API endpoint.

Params:
  path      (str) — file path to the audio file (required)
  language  (str) — ISO-639-1 hint: "zh", "en", etc. (optional)
  model     (str) — whisper model, default "whisper-1"

Requires: OPENAI_API_KEY
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from my_agent_os.skills_layer.base import Skill, skill_err, skill_ok
from my_agent_os.skills_layer.tools import register

_WORKSPACE = Path(os.getenv("AGENT_WORKSPACE_DIR", Path.home() / "AgentOS" / "workspace"))
_MAX_BYTES = 25 * 1024 * 1024  # 25 MB Whisper hard limit
_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"

_SUPPORTED: dict[str, str] = {
    ".mp3":  "audio/mpeg",
    ".mp4":  "audio/mp4",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".m4a":  "audio/m4a",
    ".wav":  "audio/wav",
    ".webm": "audio/webm",
    ".ogg":  "audio/ogg",
    ".flac": "audio/flac",
}


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    for candidate in [(_WORKSPACE / path_str).resolve(), Path.cwd() / path_str]:
        if candidate.exists():
            return candidate
    return p


@register
class AudioTranscribe(Skill):
    name = "audio_transcribe"
    description = (
        "Transcribe speech from an audio file using OpenAI Whisper. "
        "Params: path (str, required), language (str, e.g. 'zh'/'en', optional), "
        "model (str, default 'whisper-1'). "
        "Requires OPENAI_API_KEY."
    )

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        path_str = (params.get("path") or "").strip()
        if not path_str:
            return skill_err("MISSING_PARAM", "Parameter 'path' is required.")

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            return skill_err(
                "AUTH_ERROR",
                "OPENAI_API_KEY is not set. Whisper requires an OpenAI API key.",
            )

        file_path = _resolve(path_str)
        if not file_path.exists():
            return skill_err("FILE_NOT_FOUND", f"Audio file not found: {path_str}")

        suffix = file_path.suffix.lower()
        if suffix not in _SUPPORTED:
            return skill_err(
                "UNSUPPORTED_FORMAT",
                f"'{suffix}' is not supported. Supported: {sorted(_SUPPORTED)}",
            )

        audio_bytes = file_path.read_bytes()
        if len(audio_bytes) > _MAX_BYTES:
            return skill_err(
                "FILE_TOO_LARGE",
                f"Audio exceeds 25 MB limit ({len(audio_bytes) // 1_000_000} MB).",
            )
        if len(audio_bytes) < 100:
            return skill_err("FILE_TOO_SMALL", "Audio file is empty or too small.")

        whisper_model = (params.get("model") or "whisper-1").strip()
        language = (params.get("language") or "").strip()
        mime = _SUPPORTED[suffix]

        try:
            text = _call_whisper(audio_bytes, file_path.name, mime, whisper_model, language, api_key)
            return skill_ok(
                text,
                output=text,
                data={
                    "file": file_path.name,
                    "model": whisper_model,
                    "language": language or "auto",
                    "duration_hint_bytes": len(audio_bytes),
                },
            )
        except Exception as exc:
            return skill_err("TRANSCRIPTION_FAILED", f"Whisper error: {exc}")


def _call_whisper(
    audio: bytes, filename: str, mime: str, model: str, language: str, api_key: str
) -> str:
    boundary = b"----AgentOSAudioBoundary7f3a"
    parts: list[bytes] = [
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="model"\r\n\r\n' + model.encode() + b"\r\n",
    ]
    if language:
        parts.append(
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="language"\r\n\r\n'
            + language.encode() + b"\r\n"
        )
    parts += [
        b"--" + boundary + b"\r\n"
        + f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
        + f"Content-Type: {mime}\r\n\r\n".encode()
        + audio + b"\r\n",
        b"--" + boundary + b"--\r\n",
    ]
    body = b"".join(parts)

    req = urllib.request.Request(
        _WHISPER_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode())
    return result.get("text", "")
