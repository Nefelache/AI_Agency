"""
Pre-demo / production smoke: public HTTP surface, auth-guarded health, OpenClaw UI/CORS bootstrap.

Run with the full suite:
  pytest my_agent_os/tests -v

Quick gate before showing the site:
  pytest my_agent_os/tests/test_demo_full_smoke.py -v
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def demo_runtime(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Stable keys for extended health + OpenClaw WS in one process."""
    import my_agent_os.config.settings as cfg

    api_key = "demo-smoke-api-owner"
    ws_tok = "demo-smoke-openclaw-ws"
    monkeypatch.setattr(cfg.settings, "DEV_DISABLE_TOKEN_AUTH", False)
    monkeypatch.setattr(cfg.settings, "API_KEY_OWNER", api_key)
    monkeypatch.setattr(cfg.settings, "OPENCLAW_GATEWAY_TOKEN", ws_tok)
    return {"api_key": api_key, "ws_token": ws_tok}


@pytest.fixture
def client(demo_runtime: dict[str, str]):
    from my_agent_os.api_gateway.main import app

    with TestClient(app) as tc:
        yield tc


@pytest.fixture(autouse=True)
def clear_openclaw_sessions():
    import my_agent_os.api_gateway.openclaw_compat.gateway_ws as gw

    gw._session_histories.clear()
    yield
    gw._session_histories.clear()


def test_smoke_public_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "alive"


def test_smoke_web_root_serves_terminal_ui(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    # Static index is HTML
    assert "text/html" in r.headers.get("content-type", "")
    assert len(r.text) > 100


def test_smoke_openclaw_control_ui_bootstrap(client: TestClient):
    r = client.get("/openclaw/__openclaw/control-ui-config.json")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["basePath"] == "/openclaw"
    assert cfg.get("assistantName")
    assert cfg.get("serverVersion")


def test_smoke_openclaw_spa_index(client: TestClient):
    r = client.get("/openclaw/", follow_redirects=True)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "openclaw" in r.text.lower() or "<!doctype html>" in r.text.lower()


def test_smoke_openapi_docs_available(client: TestClient):
    r = client.get("/docs")
    assert r.status_code == 200
    assert "swagger" in r.text.lower() or "openapi" in r.text.lower()


def test_smoke_whatsapp_bridge_heartbeat(client: TestClient):
    r = client.post("/health/whatsapp")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_smoke_health_extended_requires_key(client: TestClient):
    r = client.get("/health/extended")
    assert r.status_code == 401


def test_smoke_health_extended_authenticated(client: TestClient, demo_runtime: dict[str, str]):
    r = client.get("/health/extended", headers={"X-API-Key": demo_runtime["api_key"]})
    assert r.status_code == 200
    data = r.json()
    assert "version" in data
    assert data["db"]["ok"] is True
    assert "llm" in data
    assert "whatsapp_bridge" in data


def _connect_payload(token: str) -> dict:
    return {
        "minProtocol": 3,
        "maxProtocol": 3,
        "client": {
            "id": "demo-smoke",
            "version": "1",
            "platform": "pytest",
            "mode": "webchat",
        },
        "role": "operator",
        "scopes": [
            "operator.admin",
            "operator.read",
            "operator.write",
            "operator.approvals",
            "operator.pairing",
        ],
        "auth": {"token": token},
    }


def test_smoke_openclaw_websocket_handshake_and_usage(client: TestClient, demo_runtime: dict[str, str]):
    tok = demo_runtime["ws_token"]
    with client.websocket_connect("/openclaw") as ws:
        assert ws.receive_json()["event"] == "connect.challenge"
        ws.send_json(
            {"type": "req", "id": "c0", "method": "connect", "params": _connect_payload(tok)}
        )
        hello = ws.receive_json()
        assert hello["ok"] is True
        assert hello["payload"]["type"] == "hello-ok"
        assert "sessions.usage" in hello["payload"]["features"]["methods"]

        ws.send_json({"type": "req", "id": "u1", "method": "sessions.usage", "params": {}})
        u = ws.receive_json()
        assert u["ok"] is True
        assert u["payload"].get("sessions") == []
        assert "aggregates" in u["payload"]


def test_demo_release_checklist_router_memory_skills_importable():
    """Light import guard so a broken package fails fast before HTTP tests."""
    from my_agent_os.agent_core import router_engine  # noqa: F401
    from my_agent_os.memory_layer import store  # noqa: F401
    from my_agent_os.skills_layer.tools import list_tools  # noqa: F401

    assert len(list_tools()) >= 1


def test_demo_chat_send_roundtrip_mocked(
    client: TestClient, demo_runtime: dict[str, str], monkeypatch: pytest.MonkeyPatch
):
    async def fake_route(*args, **kwargs):
        return {"answer": "demo-ok"}

    import my_agent_os.api_gateway.openclaw_compat.gateway_ws as gws

    monkeypatch.setattr(gws, "route", fake_route)

    tok = demo_runtime["ws_token"]
    with client.websocket_connect("/openclaw") as ws:
        ws.receive_json()
        ws.send_json(
            {"type": "req", "id": "c1", "method": "connect", "params": _connect_payload(tok)}
        )
        ws.receive_json()

        ws.send_json(
            {
                "type": "req",
                "id": "ch1",
                "method": "chat.send",
                "params": {"sessionKey": "main", "message": "ping", "idempotencyKey": "demo-1"},
            }
        )
        assert ws.receive_json()["payload"].get("status") == "started"
        evt = ws.receive_json()
        assert evt["event"] == "chat"
        assert "demo-ok" in json.dumps(evt["payload"])

