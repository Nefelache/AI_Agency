from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from my_agent_os.agent_core.router_engine import route
from my_agent_os.api_gateway.openclaw_compat import snapshots
from my_agent_os.skills_layer.tools import list_tools
from my_agent_os.api_gateway.openclaw_compat.constants import (
    GATEWAY_EVENTS,
    GATEWAY_METHODS,
    MAX_BUFFERED_BYTES,
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    TICK_INTERVAL_MS,
)
from my_agent_os.config.settings import settings
from my_agent_os.version import __version__ as APP_VERSION

logger = logging.getLogger(__name__)

_session_histories: dict[str, list[dict[str, Any]]] = {}

SendFn = Callable[[dict[str, Any]], Awaitable[None]]


def _client_looped(host: str | None) -> bool:
    if not host:
        return False
    h = host.lower()
    return h in ("127.0.0.1", "::1", "localhost") or h.startswith("127.")


def _err(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    e: dict[str, Any] = {"code": code, "message": message}
    if details:
        e["details"] = details
    return e


def _snapshot() -> dict[str, Any]:
    token = (settings.OPENCLAW_GATEWAY_TOKEN or "").strip()
    return {
        "presence": snapshots.build_system_presence(),
        "health": {},
        "stateVersion": {"presence": 1, "health": 1},
        "uptimeMs": 0,
        "configPath": "agent-os://compat",
        "stateDir": "agent-os://compat",
        "sessionDefaults": {
            "defaultAgentId": "agent-os",
            "mainKey": "main",
            "mainSessionKey": "main",
            "scope": "per-sender",
        },
        "authMode": "token" if token else "none",
    }


def _validate_connect(params: dict[str, Any] | None) -> bool:
    if not isinstance(params, dict):
        return False
    try:
        mn = int(params.get("minProtocol", 0))
        mx = int(params.get("maxProtocol", 0))
    except (TypeError, ValueError):
        return False
    if mn > PROTOCOL_VERSION or mx < PROTOCOL_VERSION:
        return False
    client = params.get("client")
    if not isinstance(client, dict):
        return False
    for k in ("id", "version", "platform", "mode"):
        if k not in client:
            return False
    if not isinstance(params.get("role"), str):
        return False
    if not isinstance(params.get("scopes"), list):
        return False
    return True


def _auth_ok(params: dict[str, Any], client_host: str | None) -> bool:
    token = (settings.OPENCLAW_GATEWAY_TOKEN or "").strip()
    auth = params.get("auth") if isinstance(params.get("auth"), dict) else {}
    req_t = auth.get("token")
    req_token = req_t.strip() if isinstance(req_t, str) else ""
    if token:
        return req_token == token
    return _client_looped(client_host)


async def handle_openclaw_gateway_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    client = websocket.client
    client_host = client[0] if client else None

    conn_id = str(uuid.uuid4())
    event_seq = 0

    async def send(obj: dict[str, Any]) -> None:
        nonlocal event_seq
        if obj.get("type") == "event" and "seq" not in obj:
            event_seq += 1
            obj = {**obj, "seq": event_seq}
        await websocket.send_text(json.dumps(obj))

    await send(
        {
            "type": "event",
            "event": "connect.challenge",
            "payload": {"nonce": str(uuid.uuid4()), "ts": int(time.time() * 1000)},
        }
    )

    handshake_done = False

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            mtype = msg.get("type")

            if not handshake_done:
                if mtype != "req" or msg.get("method") != "connect":
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "res",
                                "id": msg.get("id", "unknown"),
                                "ok": False,
                                "error": _err("INVALID_REQUEST", "first request must be connect"),
                            }
                        )
                    )
                    await websocket.close(code=4008)
                    return
                p = msg.get("params")
                if not _validate_connect(p if isinstance(p, dict) else None):
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "res",
                                "id": msg["id"],
                                "ok": False,
                                "error": _err("INVALID_REQUEST", "invalid connect params"),
                            }
                        )
                    )
                    await websocket.close(code=4008)
                    return
                params = p if isinstance(p, dict) else {}
                if not _auth_ok(params, client_host):
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "res",
                                "id": msg["id"],
                                "ok": False,
                                "error": _err(
                                    "UNAUTHORIZED",
                                    "OpenClaw compat: set OPENCLAW_GATEWAY_TOKEN and paste it in "
                                    "Control UI settings, or connect WebSocket from loopback.",
                                ),
                            }
                        )
                    )
                    await websocket.close(code=4008)
                    return

                hello = {
                    "type": "hello-ok",
                    "protocol": PROTOCOL_VERSION,
                    "server": {"version": APP_VERSION, "connId": conn_id},
                    "features": {"methods": GATEWAY_METHODS, "events": GATEWAY_EVENTS},
                    "snapshot": _snapshot(),
                    "policy": {
                        "maxPayload": MAX_PAYLOAD_BYTES,
                        "maxBufferedBytes": MAX_BUFFERED_BYTES,
                        "tickIntervalMs": TICK_INTERVAL_MS,
                    },
                }
                await websocket.send_text(
                    json.dumps({"type": "res", "id": msg["id"], "ok": True, "payload": hello})
                )
                handshake_done = True
                continue

            if mtype != "req":
                continue
            req_id = msg.get("id", "")
            method = msg.get("method", "")
            p2 = msg.get("params")
            params2: dict[str, Any] = p2 if isinstance(p2, dict) else {}
            ok, payload, error = await _dispatch_method(method, params2, send)
            if ok:
                await websocket.send_text(
                    json.dumps({"type": "res", "id": req_id, "ok": True, "payload": payload})
                )
            else:
                await websocket.send_text(
                    json.dumps({"type": "res", "id": req_id, "ok": False, "error": error})
                )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("OpenClaw compat WebSocket error: %s", e)


async def _dispatch_method(method: str, params: dict[str, Any], send: SendFn) -> tuple[bool, Any, Any]:
    if method == "chat.history":
        sk = str(params.get("sessionKey", "main"))
        messages = list(_session_histories.get(sk, []))
        return True, {"messages": messages, "thinkingLevel": None}, None

    if method == "chat.abort":
        return True, {"ok": True}, None

    if method == "chat.send":
        sk = str(params.get("sessionKey", "main"))
        message = params.get("message")
        if not isinstance(message, str) or not message.strip():
            return True, {"status": "error", "errorMessage": "empty message"}, None
        run_id = str(params.get("idempotencyKey") or uuid.uuid4())
        text_in = message.strip()

        async def _run_chat() -> None:
            user_id = f"openclaw:{sk}"
            user_ts = int(time.time() * 1000)
            try:
                out = await route(text_in, channel="openclaw", user_id=user_id, with_memory=True)
                text = ""
                if isinstance(out, dict):
                    text = str(out.get("answer") or out.get("brief") or "")
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                    "timestamp": int(time.time() * 1000),
                }
            except Exception as ex:
                logger.exception("openclaw_compat chat.send route failed")
                assistant_msg = {
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"Error: {ex}"}],
                    "timestamp": int(time.time() * 1000),
                }
            hist = _session_histories.setdefault(sk, [])
            hist.append(
                {
                    "role": "user",
                    "content": [{"type": "text", "text": text_in}],
                    "timestamp": user_ts,
                }
            )
            hist.append(assistant_msg)
            await send(
                {
                    "type": "event",
                    "event": "chat",
                    "payload": {
                        "runId": run_id,
                        "sessionKey": sk,
                        "seq": 1,
                        "state": "final",
                        "message": assistant_msg,
                    },
                }
            )

        asyncio.create_task(_run_chat())
        return True, {"status": "started", "runId": run_id}, None

    if method == "sessions.list":
        return True, snapshots.build_sessions_list(_session_histories), None

    if method in (
        "sessions.patch",
        "sessions.preview",
        "sessions.create",
        "sessions.delete",
        "sessions.reset",
        "sessions.compact",
    ):
        key = str(params.get("key", "main"))
        if method == "sessions.preview":
            return True, {"messages": _session_histories.get(key, [])}, None
        if method == "sessions.create":
            return True, {"key": key, "sessionId": f"local-{key}"}, None
        if method == "sessions.patch":
            return True, {
                "ok": True,
                "path": "agent-os://compat",
                "key": key,
                "entry": {"sessionId": key},
            }, None
        return True, {"ok": True}, None

    if method == "sessions.send":
        return (
            True,
            {"status": "skipped", "message": "Use the Chat tab; Pi sessions.send is not bridged."},
            None,
        )

    if method == "sessions.abort":
        return True, {"ok": True}, None

    if method == "channels.status":
        return True, snapshots.build_channels_status(), None

    if method == "channels.logout":
        return True, {"ok": True, "message": "noop — manage Baileys auth in whatsapp-bridge container"}, None

    if method == "config.get":
        return True, snapshots.build_config_snapshot(), None

    if method == "config.schema":
        return (
            True,
            {
                "schema": {"type": "object", "properties": {}},
                "uiHints": {},
                "version": "agent-os-compat",
                "generatedAt": "",
            },
            None,
        )

    if method == "config.schema.lookup":
        return True, {"schema": {}}, None

    if method in ("config.set", "config.patch", "config.apply"):
        return (
            False,
            None,
            _err(
                "NOT_IMPLEMENTED",
                "Read-only compat gateway — edit my_agent_os/config/channels.yaml and my_agent_os/config/.env.",
            ),
        )

    if method == "agents.list":
        return (
            True,
            {
                "defaultId": "agent-os",
                "mainKey": "main",
                "scope": "per-sender",
                "agents": [
                    {
                        "id": "agent-os",
                        "name": "Agent OS",
                        "workspace": "default",
                        "model": {"primary": settings.DEEPSEEK_MODEL} if settings.DEEPSEEK_API_KEY else {},
                    }
                ],
            },
            None,
        )

    if method == "models.list":
        return True, snapshots.build_models_list(), None

    if method == "skills.status":
        return True, snapshots.build_skills_status(), None

    if method == "skills.bins":
        return True, {"bins": []}, None

    if method == "skills.update":
        return True, {"ok": True}, None

    if method == "skills.install":
        return True, {"ok": True, "message": "install not implemented in compat gateway"}, None

    if method == "system-presence":
        return True, snapshots.build_system_presence(), None

    if method == "status":
        return True, snapshots.build_status_payload(), None

    if method == "health":
        return True, snapshots.build_health_payload(), None

    if method == "cron.list":
        return True, {"jobs": [], "total": 0, "hasMore": False}, None

    if method == "cron.status":
        return True, snapshots.build_cron_status(), None

    if method in ("cron.add", "cron.update", "cron.remove", "cron.run"):
        return (
            False,
            None,
            _err("NOT_IMPLEMENTED", "Cron is not wired in Agent OS compat gateway."),
        )

    if method == "cron.runs":
        return True, {"entries": [], "total": 0, "hasMore": False}, None

    if method == "node.list":
        return True, {"nodes": []}, None

    if method == "logs.tail":
        return True, snapshots.tail_gateway_logs(params), None

    if method == "usage.status":
        return True, snapshots.build_usage_status(), None

    if method == "sessions.usage":
        return True, snapshots.build_sessions_usage(params), None

    if method == "sessions.usage.timeseries":
        return True, snapshots.build_sessions_usage_timeseries(str(params.get("key", ""))), None

    if method == "sessions.usage.logs":
        return True, snapshots.build_sessions_usage_logs(), None

    if method == "usage.cost":
        return True, snapshots.build_usage_cost(), None

    if method == "agent.wait":
        return True, {"status": "timeout"}, None

    if method in (
        "sessions.subscribe",
        "sessions.unsubscribe",
        "sessions.messages.subscribe",
        "sessions.messages.unsubscribe",
    ):
        return True, {"ok": True}, None

    if method == "connect":
        return False, None, _err("INVALID_REQUEST", "duplicate connect")

    if method == "gateway.identity.get":
        return True, {"deviceId": "agent-os-compat", "publicKey": ""}, None

    if method == "last-heartbeat":
        return True, None, None

    if method in ("wake", "send", "agent"):
        return True, {"status": "noop", "message": "compat gateway — use Chat tab"}, None

    if method == "agent.identity.get":
        return True, {"agentId": "agent-os", "name": "Agent OS", "avatar": ""}, None

    if method in ("talk.config", "talk.speak", "talk.mode", "tts.status", "tts.providers"):
        return True, {}, None

    if method in ("tts.enable", "tts.disable", "tts.convert", "tts.setProvider"):
        return (
            False,
            None,
            _err("NOT_IMPLEMENTED", "TTS not available in compat gateway."),
        )

    if method in ("wizard.start", "wizard.next", "wizard.cancel", "wizard.status"):
        return True, {"status": "idle"}, None

    if method in ("secrets.reload", "secrets.resolve"):
        return True, {"ok": True}, None

    if method == "update.run":
        return False, None, _err("NOT_IMPLEMENTED", "Use your package manager / git to update Agent OS.")

    if method in ("node.invoke", "node.event", "node.pending.pull"):
        return False, None, _err("NOT_IMPLEMENTED", "Nodes are not supported in compat gateway.")

    if method in ("tools.catalog", "tools.effective"):
        tools = [{"name": x["name"], "description": x.get("description", "")} for x in list_tools()]
        return True, {"tools": tools}, None

    if method == "doctor.memory.status":
        from pathlib import Path

        p = Path(settings.MEMORY_DB_PATH)
        return True, {"ok": p.exists(), "path": str(p)}, None

    if method.startswith("exec.approvals"):
        return True, {}, None

    return True, {}, None
