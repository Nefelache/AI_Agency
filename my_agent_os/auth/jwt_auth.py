"""
JWT Authentication — issue and validate tokens for multi-user access.

Algorithm: HS256 with configurable secret.
Token payload: {sub: user_id, plan: str, role: str, iat: int, exp: int}

Implemented without python-jose to keep the dependency tree minimal;
uses PyJWT if available, otherwise falls back to a pure-Python HS256.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from my_agent_os.config.settings import settings

_SECRET = (getattr(settings, "JWT_SECRET", None) or os.getenv("JWT_SECRET", "change-me-in-prod")).encode()
_ALG    = "HS256"
_EXPIRE = 7 * 24 * 3600   # 7 days


# ── Pure-Python HS256 implementation ─────────────────────────────

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * pad)


def encode_token(payload: dict[str, Any], expire_in: int = _EXPIRE) -> str:
    """Create a signed JWT token."""
    now  = int(time.time())
    full = {**payload, "iat": now, "exp": now + expire_in}
    header  = _b64url_encode(json.dumps({"alg": _ALG, "typ": "JWT"}).encode())
    body    = _b64url_encode(json.dumps(full, ensure_ascii=False).encode())
    sig_input = f"{header}.{body}".encode()
    sig       = _b64url_encode(hmac.new(_SECRET, sig_input, hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


def decode_token(token: str) -> dict[str, Any]:
    """
    Verify and decode a JWT token.
    Raises ValueError for invalid/expired tokens.
    """
    try:
        header_b64, body_b64, sig_b64 = token.strip().split(".")
    except ValueError:
        raise ValueError("Malformed token.")

    sig_input  = f"{header_b64}.{body_b64}".encode()
    expected   = _b64url_encode(hmac.new(_SECRET, sig_input, hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig_b64):
        raise ValueError("Invalid token signature.")

    payload = json.loads(_b64url_decode(body_b64))
    if payload.get("exp", 0) < int(time.time()):
        raise ValueError("Token expired.")
    return payload
