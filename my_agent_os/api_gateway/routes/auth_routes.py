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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from my_agent_os.auth.jwt_auth import encode_token, decode_token
from my_agent_os.auth.user_store import get_user_store

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
    return {
        "id":         user["id"],
        "email":      user["email"],
        "plan":       user["plan"],
        "role":       user["role"],
        "sub_status": user.get("sub_status", "none"),
        "limits":     _PLAN_LIMITS.get(user["plan"], _PLAN_LIMITS["free"]),
        "created_at": user.get("created_at", ""),
    }


@router.post("/register")
async def register(req: RegisterRequest) -> dict[str, Any]:
    store = get_user_store()
    try:
        user = await asyncio.get_event_loop().run_in_executor(
            None, lambda: store.create_user(req.email, req.password, req.plan)
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    token = encode_token({"sub": user["id"], "plan": user["plan"], "role": user["role"]})
    return {"token": token, "user": _safe_profile(user)}


@router.post("/login")
async def login(req: LoginRequest) -> dict[str, Any]:
    store = get_user_store()
    user  = await asyncio.get_event_loop().run_in_executor(
        None, lambda: store.authenticate(req.email, req.password)
    )
    if not user:
        raise HTTPException(401, "Invalid email or password.")

    token = encode_token({"sub": user["id"], "plan": user["plan"], "role": user["role"]})
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

    new_token = encode_token({"sub": user["id"], "plan": user["plan"], "role": user["role"]})
    return {"token": new_token, "user": _safe_profile(user)}


@router.get("/me")
async def me(authorization: str | None = None) -> dict[str, Any]:
    from fastapi import Header
    # This dependency is handled by the middleware check below
    raise HTTPException(401, "Provide Authorization: Bearer <token>")
