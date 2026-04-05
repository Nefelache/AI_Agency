"""
WeCom (企业微信) — webhook stub for corp message / bot callbacks.

Wire your enterprise WeChat app callback URL to POST /wecom/callback.
Implement signature verification and XML/JSON parsing per Tencent docs when ready.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wecom", tags=["WeCom"])


@router.get("/callback")
async def wecom_verify(echostr: str | None = None, msg_signature: str | None = None) -> Any:
    """URL verification (echo mode) — return echostr when you add token validation."""
    return echostr or "ok"


@router.post("/callback")
async def wecom_callback(request: Request) -> dict[str, Any]:
    """
    Inbound messages from WeCom. Stub: logs and returns ok.
    Next step: parse body, verify signature, map to route(..., channel='wecom', user_id=...).
    """
    try:
        body = await request.body()
        logger.info("WeCom callback received (%d bytes)", len(body))
    except Exception as e:
        logger.warning("WeCom read body: %s", e)
    return {"errcode": 0, "errmsg": "ok", "note": "stub — implement parser + route()"}
