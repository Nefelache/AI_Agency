"""
Web Terminal — deep-dive console for desktop sessions.

Provides full context retrieval, memory querying, and heavy-duty planning.
This is where "Control Aesthetic" meets full information density.
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from my_agent_os.auth.dependencies import get_auth_context, rate_limit_check
from my_agent_os.auth.models import AuthContext

router = APIRouter(dependencies=[Depends(rate_limit_check)])


class ConsoleQuery(BaseModel):
    query: str = Field(..., description="Free-form deep query from the web console")
    include_memory: bool = Field(True, description="Pull related docs from memory_layer")
    force_crew: bool = Field(False, description="Force multi-agent crew (show Department Views)")


class ConsoleResponse(BaseModel):
    answer: str
    sources: list[dict] | None = None
    next_actions: list[str]
    crew_views: dict[str, str] | None = None
    agent_trace: list[str] | None = None  # step-by-step trace from agentic loop


def _coerce_console_payload(result: dict[str, Any]) -> dict[str, Any]:
    """LLMs often return sources as strings or mixed shapes — avoid response_model 500s."""
    answer = result.get("answer") or result.get("brief") or ""
    if not isinstance(answer, str):
        answer = str(answer)

    raw_src = result.get("sources")
    sources: list[dict] | None = None
    if isinstance(raw_src, list) and raw_src:
        cleaned = [x for x in raw_src if isinstance(x, dict)]
        sources = cleaned or None

    raw_next = result.get("next_actions")
    if isinstance(raw_next, list):
        next_actions = [str(x) for x in raw_next]
    elif raw_next:
        next_actions = [str(raw_next)]
    else:
        next_actions = []

    raw_crew = result.get("crew_views")
    crew_views: dict[str, str] | None = None
    if isinstance(raw_crew, dict) and raw_crew:
        crew_views = {str(k): str(v) for k, v in raw_crew.items() if v is not None}

    raw_trace = result.get("agent_trace")
    agent_trace: list[str] | None = None
    if isinstance(raw_trace, list) and raw_trace:
        agent_trace = [str(s) for s in raw_trace if s]

    return {
        "answer": answer,
        "sources": sources,
        "next_actions": next_actions,
        "crew_views": crew_views,
        "agent_trace": agent_trace,
    }


@router.post("/query", response_model=ConsoleResponse)
async def handle_console(
    q: ConsoleQuery,
    auth: AuthContext = Depends(get_auth_context),
):
    from my_agent_os.agent_core.router_engine import route

    result = await route(
        raw_input=q.query,
        channel="console",
        user_id=auth.user_id,
        with_memory=q.include_memory,
        force_crew=q.force_crew,
    )
    coerced = _coerce_console_payload(result if isinstance(result, dict) else {})
    return ConsoleResponse(**coerced)
