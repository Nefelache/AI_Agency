"""
Voice Routes — Whisper transcription (STT) and TTS response.

POST /voice/transcribe  — upload audio blob → returns text transcript
POST /voice/speak       — text → TTS audio (via OpenAI TTS or gTTS fallback)

Requires:
  OPENAI_API_KEY — for Whisper API and OpenAI TTS
  TTS_MODEL      — 'tts-1' | 'tts-1-hd' (default tts-1)
  TTS_VOICE      — alloy|echo|fable|onyx|nova|shimmer (default nova)
"""

from __future__ import annotations

import io
import json
import os
import urllib.request
from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from my_agent_os.auth.dependencies import get_auth_context

router = APIRouter(prefix="/voice", tags=["Voice"])

_OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
_TTS_MODEL  = os.getenv("TTS_MODEL", "tts-1")
_TTS_VOICE  = os.getenv("TTS_VOICE", "nova")
_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
_TTS_URL     = "https://api.openai.com/v1/audio/speech"

_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB (Whisper limit)


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> dict[str, Any]:
    """
    Accept a WebM/Ogg/mp4/wav blob from the browser MediaRecorder API
    and return the Whisper transcription.
    """
    if not _OPENAI_KEY:
        raise HTTPException(503, "OPENAI_API_KEY not set — voice transcription unavailable.")

    audio_data = await file.read()
    if len(audio_data) > _MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio file exceeds 25 MB limit.")
    if len(audio_data) < 100:
        raise HTTPException(400, "Audio file too small — nothing to transcribe.")

    filename   = file.filename or "audio.webm"
    content_type = file.content_type or "audio/webm"

    try:
        boundary  = b"----AgentOSBoundary"
        body_parts = [
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="model"\r\n\r\nwhisper-1\r\n',
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="response_format"\r\n\r\njson\r\n',
            b"--" + boundary + b"\r\n"
            + f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
            + f"Content-Type: {content_type}\r\n\r\n".encode()
            + audio_data + b"\r\n",
            b"--" + boundary + b"--\r\n",
        ]
        body = b"".join(body_parts)

        req = urllib.request.Request(
            _WHISPER_URL,
            data=body,
            headers={
                "Authorization":  f"Bearer {_OPENAI_KEY}",
                "Content-Type":   f"multipart/form-data; boundary={boundary.decode()}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())

        return {"success": True, "text": result.get("text", ""), "language": result.get("language", "")}
    except Exception as e:
        raise HTTPException(500, f"Transcription failed: {e}")


class SpeakRequest(BaseModel):
    text:  str
    voice: str = _TTS_VOICE
    model: str = _TTS_MODEL


@router.post("/speak")
async def text_to_speech(
    req: SpeakRequest,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> Response:
    """
    Convert text to speech using OpenAI TTS.
    Returns audio/mpeg binary (mp3).
    """
    if not _OPENAI_KEY:
        raise HTTPException(503, "OPENAI_API_KEY not set — TTS unavailable.")

    text = req.text.strip()[:4096]
    if not text:
        raise HTTPException(400, "Empty text.")

    try:
        payload = json.dumps({
            "model": req.model,
            "input": text,
            "voice": req.voice,
        }).encode()
        api_req = urllib.request.Request(
            _TTS_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {_OPENAI_KEY}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(api_req, timeout=30) as resp:
            audio_bytes = resp.read()

        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=response.mp3"},
        )
    except Exception as e:
        raise HTTPException(500, f"TTS failed: {e}")
