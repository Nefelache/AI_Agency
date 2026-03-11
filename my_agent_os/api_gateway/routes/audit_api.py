"""
Audit API — owner-only search and listing for audit JSONL logs.

Storage: my_agent_os/memory_layer/data/audit/audit_YYYY-MM-DD.jsonl
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from my_agent_os.auth.dependencies import require_role
from my_agent_os.auth.models import AuthContext, Role
from my_agent_os.enterprise.audit import audit_dir

router = APIRouter(prefix="/audit", tags=["Audit"])


def _iter_files(date: str | None = None) -> list[Path]:
    d = audit_dir()
    if date:
        return [d / f"audit_{date}.jsonl"]
    return sorted(d.glob("audit_*.jsonl"), reverse=True)


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    def _gen():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    return _gen()


@router.get("/dates")
async def list_dates(auth: AuthContext = Depends(require_role(Role.OWNER))) -> dict[str, Any]:
    d = audit_dir()
    dates = []
    for p in sorted(d.glob("audit_*.jsonl"), reverse=True):
        name = p.name.removeprefix("audit_").removesuffix(".jsonl")
        dates.append(name)
    return {"dates": dates}


class AuditSearchRequest(BaseModel):
    date: str | None = Field(None, description="YYYY-MM-DD")
    session_id: str | None = None
    channel: str | None = None
    user_id: str | None = None
    event: str | None = None
    limit: int = Field(200, ge=1, le=2000)


@router.post("/search")
async def search_audit(
    req: AuditSearchRequest,
    auth: AuthContext = Depends(require_role(Role.OWNER)),
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for path in _iter_files(req.date):
        if req.date and not path.exists():
            raise HTTPException(404, f"No audit log for date {req.date}")
        for entry in _iter_jsonl(path):
            if req.event and entry.get("event") != req.event:
                continue
            if req.session_id and entry.get("session_id") != req.session_id:
                continue
            if req.channel and entry.get("channel") != req.channel:
                continue
            if req.user_id and entry.get("user_id") != req.user_id:
                continue
            results.append(entry)
            if len(results) >= req.limit:
                return {"results": results, "truncated": True}
    return {"results": results, "truncated": False}

