"""
Memory API — REST endpoints for memory inspection and management.

Auth: read endpoints require OWNER or CHANNEL role.
      write/delete endpoints require OWNER role only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from my_agent_os.auth.dependencies import get_auth_context, require_role, rate_limit_check
from my_agent_os.auth.models import AuthContext, Role

router = APIRouter(dependencies=[Depends(rate_limit_check)])

_engine = None


def set_engine(engine) -> None:
    global _engine
    _engine = engine


def _get_engine():
    if _engine is None:
        raise HTTPException(503, "Memory engine not initialized")
    return _engine


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(10, ge=1, le=50)


class MemoryOut(BaseModel):
    id: str
    memory_type: str
    content: str
    summary: str | None = None
    key_points: list[str] = []
    entities: list[str] = []
    priority: float
    access_count: int
    created_at: str
    updated_at: str


class SessionOut(BaseModel):
    id: str
    status: str
    topic: str | None = None
    summary: str | None = None
    turn_count: int
    created_at: str
    sealed_at: str | None = None


@router.get("/stats")
async def get_stats(auth: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    engine = _get_engine()
    return await engine.stats(auth.user_id)


@router.get("/list")
async def list_memories(
    limit: int = 50,
    auth: AuthContext = Depends(get_auth_context),
) -> list[MemoryOut]:
    engine = _get_engine()
    records = await engine.get_all_memories(auth.user_id, limit)
    return [_memory_to_out(r) for r in records]


@router.get("/sessions")
async def list_sessions(
    limit: int = 20,
    auth: AuthContext = Depends(get_auth_context),
) -> list[SessionOut]:
    engine = _get_engine()
    sessions = await engine.list_sessions(auth.user_id, limit)
    return [
        SessionOut(
            id=s.id,
            status=s.status.value,
            topic=s.topic,
            summary=s.summary,
            turn_count=s.turn_count,
            created_at=s.created_at.isoformat(),
            sealed_at=s.sealed_at.isoformat() if s.sealed_at else None,
        )
        for s in sessions
    ]


@router.post("/search")
async def search_memories(
    req: SearchRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> list[MemoryOut]:
    engine = _get_engine()
    records = await engine.search_memories(auth.user_id, req.query, req.top_k)
    return [_memory_to_out(r) for r in records]


@router.post("/seal")
async def seal_session(
    auth: AuthContext = Depends(require_role(Role.OWNER)),
) -> dict[str, Any]:
    engine = _get_engine()
    return await engine.force_seal_session(auth.user_id)


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    auth: AuthContext = Depends(require_role(Role.OWNER)),
) -> dict[str, str]:
    engine = _get_engine()
    await engine.delete_memory(memory_id)
    return {"status": "deleted", "id": memory_id}


def _memory_to_out(r) -> MemoryOut:
    return MemoryOut(
        id=r.id,
        memory_type=r.memory_type.value,
        content=r.content,
        summary=r.summary,
        key_points=r.key_points,
        entities=r.entities,
        priority=round(r.priority, 3),
        access_count=r.access_count,
        created_at=r.created_at.isoformat(),
        updated_at=r.updated_at.isoformat(),
    )
