"""
Web Terminal — deep-dive console for desktop sessions.

Provides full context retrieval, memory querying, and heavy-duty planning.
This is where "Control Aesthetic" meets full information density.
"""

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
    return ConsoleResponse(
        answer=result.get("answer", ""),
        sources=result.get("sources"),
        next_actions=result.get("next_actions", []),
        crew_views=result.get("crew_views"),
    )
