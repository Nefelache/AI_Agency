"""
Auth Routes — register, login, refresh, and profile management.

POST /auth/register  — create new account
POST /auth/login     — returns JWT access token
POST /auth/refresh   — exchange valid token for fresh one
GET  /auth/me        — current user profile
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from my_agent_os.auth.dependencies import get_auth_context
from my_agent_os.auth.jwt_auth import encode_token, decode_token
from my_agent_os.auth.models import AuthContext
from my_agent_os.auth.user_store import get_user_store
from my_agent_os.config.settings import settings

router = APIRouter(prefix="/auth", tags=["Auth"])

_PLAN_LIMITS = {
    "free":       {"rpm": 20,  "memory_limit": 500},
    "pro":        {"rpm": 120, "memory_limit": 10_000},
    "enterprise": {"rpm": 600, "memory_limit": 100_000},
}


class RegisterRequest(BaseModel):
    email:    str
    password: str
    plan:     str = "free"


class LoginRequest(BaseModel):
    email:    str
    password: str


class RefreshRequest(BaseModel):
    token: str


def _safe_profile(user: dict[str, Any]) -> dict[str, Any]:
    """Strip sensitive fields before returning to client."""
    raw_role = (user.get("role") or "employee").lower()
    display_role = "root" if raw_role in ("root", "owner") else "employee"
    return {
        "id":         user["id"],
        "email":      user["email"],
        "plan":       user["plan"],
        "role":       display_role,
        "sub_status": user.get("sub_status", "none"),
        "limits":     _PLAN_LIMITS.get(user["plan"], _PLAN_LIMITS["free"]),
        "created_at": user.get("created_at", ""),
    }


def _role_claim_for_token(user: dict[str, Any]) -> str:
    r = (user.get("role") or "employee").lower()
    if r in ("root", "owner"):
        return "root"
    return "employee"


@router.post("/register")
async def register(req: RegisterRequest) -> dict[str, Any]:
    if not settings.AUTH_ALLOW_PUBLIC_REGISTER:
        raise HTTPException(403, "Public registration is disabled.")
    store = get_user_store()
    try:
        user = await asyncio.get_event_loop().run_in_executor(
            None, lambda: store.create_user(req.email, req.password, req.plan, role="employee")
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    rc = _role_claim_for_token(user)
    token = encode_token({"sub": user["id"], "plan": user["plan"], "role": rc})
    return {"token": token, "user": _safe_profile(user)}


@router.post("/login")
async def login(req: LoginRequest) -> dict[str, Any]:
    store = get_user_store()
    user  = await asyncio.get_event_loop().run_in_executor(
        None, lambda: store.authenticate(req.email, req.password)
    )
    if not user:
        raise HTTPException(401, "Invalid email or password.")

    rc = _role_claim_for_token(user)
    token = encode_token({"sub": user["id"], "plan": user["plan"], "role": rc})
    return {"token": token, "user": _safe_profile(user)}


@router.post("/refresh")
async def refresh_token(req: RefreshRequest) -> dict[str, Any]:
    try:
        payload = decode_token(req.token)
    except ValueError as e:
        raise HTTPException(401, str(e))

    store = get_user_store()
    user  = await asyncio.get_event_loop().run_in_executor(
        None, lambda: store.get_user_by_id(payload["sub"])
    )
    if not user:
        raise HTTPException(401, "User not found.")

    rc = _role_claim_for_token(user)
    new_token = encode_token({"sub": user["id"], "plan": user["plan"], "role": rc})
    return {"token": new_token, "user": _safe_profile(user)}


@router.get("/session")
async def session(auth: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    """Who am I for the current JWT or API key (drives UI caps e.g. seal session)."""
    return {"user_id": auth.user_id, "role": auth.role.value}


@router.get("/me")
async def me(authorization: str | None = Header(None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authorization: Bearer <token> required.")
    try:
        payload = decode_token(authorization[7:].strip())
    except ValueError as e:
        raise HTTPException(401, str(e))

    store = get_user_store()
    user = await asyncio.get_event_loop().run_in_executor(
        None, lambda: store.get_user_by_id(payload["sub"])
    )
    if not user:
        raise HTTPException(401, "User not found.")
    return {"user": _safe_profile(user)}
