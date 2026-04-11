"""
Auth Dependencies — FastAPI Depends for API Key validation, RBAC, and rate limiting.

Security principles (learned from OpenClaw CVE-2026-25253):
  - Zero trust: every request validates identity, including localhost
  - Rate limiting: sliding window per key, prevents brute-force
  - Least privilege: require_role() guards destructive endpoints
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable

from fastapi import Header, HTTPException, Request, Depends

from my_agent_os.auth.jwt_auth import decode_token
from my_agent_os.auth.models import AuthContext, Role
from my_agent_os.config.settings import settings


def _build_key_table() -> dict[str, dict]:
    """Build the API key lookup table from settings."""
    table: dict[str, dict] = {}
    if settings.API_KEY_OWNER:
        table[settings.API_KEY_OWNER] = {"user_id": "owner", "role": Role.ROOT}
    if settings.API_KEY_CHANNEL:
        table[settings.API_KEY_CHANNEL] = {"user_id": "channel_bot", "role": Role.CHANNEL}
    if settings.API_KEY_GUEST:
        table[settings.API_KEY_GUEST] = {"user_id": "guest", "role": Role.GUEST}
    return table


def _role_from_jwt_claim(role_str: str | None) -> Role:
    r = (role_str or "employee").lower()
    if r in ("root", "owner", "admin"):
        return Role.ROOT
    if r == "employee":
        return Role.EMPLOYEE
    if r == "channel":
        return Role.CHANNEL
    if r == "guest":
        return Role.GUEST
    return Role.EMPLOYEE


def auth_context_from_jwt_token(token: str) -> AuthContext | None:
    """Return context if token is a valid JWT; otherwise None (caller may try API key)."""
    token = token.strip()
    if token.count(".") != 2:
        return None
    try:
        payload = decode_token(token)
    except ValueError:
        return None
    uid = payload.get("sub")
    if not uid or not isinstance(uid, str):
        return None
    role = _role_from_jwt_claim(str(payload.get("role", "")))
    return AuthContext(user_id=uid, role=role, api_key_id="jwt")


async def get_auth_context(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> AuthContext:
    """
    Primary: configured API keys (X-API-Key or Authorization: Bearer <key>).
    Secondary: Bearer <JWT> for billing / multi-user APIs when the string is not a known key.
    """
    table = _build_key_table()
    bearer: str | None = None
    if authorization and authorization.startswith("Bearer "):
        bearer = authorization[7:].strip()

    if x_api_key:
        entry = table.get(x_api_key)
        if entry:
            return AuthContext(
                user_id=entry["user_id"],
                role=entry["role"],
                api_key_id=x_api_key[:8] + "...",
            )

    if bearer:
        entry = table.get(bearer)
        if entry:
            return AuthContext(
                user_id=entry["user_id"],
                role=entry["role"],
                api_key_id=bearer[:8] + "...",
            )
        ctx = auth_context_from_jwt_token(bearer)
        if ctx is not None:
            return ctx

    if not x_api_key and not bearer:
        raise HTTPException(401, "Missing API key. Send X-API-Key or Authorization header.")

    raise HTTPException(403, "Invalid API key or token.")


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


def require_admin() -> Callable:
    """ROOT-only guards (memory delete, audit, maintenance, etc.)."""
    return require_role(Role.ROOT)


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
    authorization: str | None = Header(None),
):
    """Dependency that enforces rate limiting per API key, JWT subject, or IP."""
    limiter = get_rate_limiter()
    table = _build_key_table()
    ident: str | None = x_api_key
    if not ident and authorization and authorization.startswith("Bearer "):
        tok = authorization[7:].strip()
        if tok in table:
            ident = tok[:48]
        else:
            ctx = auth_context_from_jwt_token(tok)
            if ctx is not None:
                ident = f"jwt:{ctx.user_id}"
            elif tok:
                ident = tok[:24]
    if not ident:
        ident = request.client.host if request.client else "unknown"
    if not limiter.check(ident):
        raise HTTPException(429, "Rate limit exceeded. Try again shortly.")
