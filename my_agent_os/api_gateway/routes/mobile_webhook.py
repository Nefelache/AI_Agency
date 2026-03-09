"""
Mobile Webhook — lightweight "approve / reject" channel.

Design intent: minimum cognitive friction.
Response payloads are stripped to essential A/B choices only.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from my_agent_os.auth.dependencies import get_auth_context, rate_limit_check
from my_agent_os.auth.models import AuthContext, Role

router = APIRouter(dependencies=[Depends(rate_limit_check)])


class MobileCommand(BaseModel):
    source: str = Field(..., description="Origin platform: slack | wecom | shortcut")
    payload: str = Field(..., description="Raw text or callback_id from the mobile client")


class MobileResponse(BaseModel):
    action: str
    options: list[str] | None = None
    brief: str


@router.post("/webhook", response_model=MobileResponse)
async def handle_mobile(
    cmd: MobileCommand,
    auth: AuthContext = Depends(get_auth_context),
):
    from my_agent_os.agent_core.router_engine import route

    result = await route(
        raw_input=cmd.payload,
        channel="mobile",
        user_id=auth.user_id,
    )
    return MobileResponse(
        action=result.get("action", "acknowledge"),
        options=result.get("options"),
        brief=result.get("brief", "Done."),
    )
