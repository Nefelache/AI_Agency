"""
Auth Dependencies — FastAPI Depends for API Key validation, RBAC, and rate limiting.

Security principles (learned from OpenClaw CVE-2026-25253):
  - Zero trust: every request validates identity, including localhost
  - Rate limiting: sliding window per key, prevents brute-force
  - Least privilege: require_role() guards destructive endpoints
"""

from __future__ import annotations

import time
import secrets
from collections import defaultdict
from typing import Callable

from fastapi import Header, HTTPException, Request, Depends

from my_agent_os.auth.models import AuthContext, Role, has_permission
from my_agent_os.config.settings import settings


def _build_key_table() -> dict[str, dict]:
    """Build the API key lookup table from settings."""
    table: dict[str, dict] = {}
    if settings.API_KEY_OWNER:
        table[settings.API_KEY_OWNER] = {"user_id": "owner", "role": Role.OWNER}
    if settings.API_KEY_CHANNEL:
        table[settings.API_KEY_CHANNEL] = {"user_id": "channel_bot", "role": Role.CHANNEL}
    if settings.API_KEY_GUEST:
        table[settings.API_KEY_GUEST] = {"user_id": "guest", "role": Role.GUEST}
    return table


async def get_auth_context(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> AuthContext:
    """
    Extract and validate identity from request headers.
    Accepts either X-API-Key header or Authorization: Bearer <key>.
    """
    key = x_api_key
    if not key and authorization and authorization.startswith("Bearer "):
        key = authorization[7:].strip()

    if not key:
        raise HTTPException(401, "Missing API key. Send X-API-Key header.")

    table = _build_key_table()
    entry = table.get(key)
    if not entry:
        raise HTTPException(403, "Invalid API key.")

    return AuthContext(
        user_id=entry["user_id"],
        role=entry["role"],
        api_key_id=key[:8] + "...",
    )


def require_role(*roles: Role) -> Callable:
    """Returns a dependency that enforces role membership."""
    async def _check(auth: AuthContext = Depends(get_auth_context)):
        if auth.role not in roles:
            raise HTTPException(
                403,
                f"Insufficient permissions. Required: {[r.value for r in roles]}",
            )
        return auth
    return _check


class RateLimiter:
    """
    In-memory sliding-window rate limiter.
    Keyed by API key prefix to prevent brute-force attacks.
    """

    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - self._window
        hits = self._hits[key]
        self._hits[key] = [t for t in hits if t > window_start]
        if len(self._hits[key]) >= self._max:
            return False
        self._hits[key].append(now)
        return True


_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(
            max_requests=settings.RATE_LIMIT_PER_MINUTE,
            window_seconds=60,
        )
    return _rate_limiter


async def rate_limit_check(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """Dependency that enforces rate limiting per API key or IP."""
    limiter = get_rate_limiter()
    ident = x_api_key or request.client.host if request.client else "unknown"
    if not limiter.check(ident):
        raise HTTPException(429, "Rate limit exceeded. Try again shortly.")
