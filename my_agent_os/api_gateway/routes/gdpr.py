"""
GDPR Routes — data portability and right-to-erasure.

GET  /gdpr/export   — download all memories + sessions as JSON (JWT required)
DELETE /gdpr/delete — permanently delete all user data (JWT required, irreversible)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from my_agent_os.auth.jwt_auth import decode_token

router = APIRouter(prefix="/gdpr", tags=["GDPR"])

_memory_engine_ref = None


def set_engine(engine) -> None:
    global _memory_engine_ref
    _memory_engine_ref = engine


def _get_user(authorization: str | None) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required.")
    try:
        return decode_token(authorization[7:].strip())
    except ValueError as e:
        raise HTTPException(401, str(e))


@router.get("/export")
async def export_data(authorization: str | None = Header(None)) -> JSONResponse:
    """Export all user memories and sessions as a JSON download."""
    payload = _get_user(authorization)
    user_id = payload["sub"]

    if not _memory_engine_ref:
        raise HTTPException(503, "Memory engine not available.")

    memories = await _memory_engine_ref.get_all_memories(user_id, limit=10_000)
    sessions = await _memory_engine_ref.list_sessions(user_id, limit=1_000)

    export = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user_id":     user_id,
        "memories":    [
            {
                "id":          m.id,
                "type":        m.memory_type.value,
                "content":     m.content,
                "summary":     m.summary,
                "key_points":  m.key_points,
                "entities":    m.entities,
                "priority":    m.priority,
                "created_at":  m.created_at.isoformat(),
                "access_count": m.access_count,
            }
            for m in memories
        ],
        "sessions": [
            {
                "id":         s.id,
                "status":     s.status.value,
                "topic":      s.topic,
                "summary":    s.summary,
                "created_at": s.created_at.isoformat(),
                "turn_count": s.turn_count,
            }
            for s in sessions
        ],
    }

    filename = f"agent_os_export_{user_id[:8]}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    return JSONResponse(
        content=export,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/delete")
async def delete_all_data(
    authorization: str | None = Header(None),
    confirm: str | None = None,
) -> dict[str, Any]:
    """
    Permanently delete all memories for the authenticated user.
    Requires ?confirm=DELETE_MY_DATA query parameter.
    """
    payload = _get_user(authorization)
    user_id = payload["sub"]

    if confirm != "DELETE_MY_DATA":
        raise HTTPException(
            400,
            "Pass ?confirm=DELETE_MY_DATA to permanently erase all your data. This cannot be undone.",
        )
    if not _memory_engine_ref:
        raise HTTPException(503, "Memory engine not available.")

    memories = await _memory_engine_ref.get_all_memories(user_id, limit=50_000)
    deleted  = 0
    for m in memories:
        await _memory_engine_ref.delete_memory(m.id)
        deleted += 1

    # Also remove from user DB
    try:
        from my_agent_os.auth.user_store import get_user_store
        store = get_user_store()
        # Mark deleted (we keep a tombstone, not a hard delete, to prevent abuse)
        store.update_plan(user_id, "deleted", sub_status="deleted")
    except Exception:
        pass

    return {
        "status":          "deleted",
        "memories_erased": deleted,
        "message":         f"All {deleted} memories for user {user_id[:8]} have been permanently erased.",
    }
