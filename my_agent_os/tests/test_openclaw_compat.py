"""Tests for OpenClaw Control UI compat (HTTP bootstrap, WS handshake, snapshots)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def openclaw_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """TestClient WS client host is not loopback; use gateway token auth."""
    import my_agent_os.config.settings as cfg

    tok = "pytest-openclaw-ws-token"
    monkeypatch.setattr(cfg.settings, "OPENCLAW_GATEWAY_TOKEN", tok)
    return tok


@pytest.fixture
def api_client(openclaw_token: str):
    from my_agent_os.api_gateway.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def clear_openclaw_chat_sessions():
    import my_agent_os.api_gateway.openclaw_compat.gateway_ws as gw

    gw._session_histories.clear()
    yield
    gw._session_histories.clear()


def test_control_ui_bootstrap_json(api_client: TestClient):
    r = api_client.get("/openclaw/__openclaw/control-ui-config.json")
    assert r.status_code == 200
    data = r.json()
    assert data["basePath"] == "/openclaw"
    assert "assistantName" in data
    assert "serverVersion" in data


def test_openclaw_redirect(api_client: TestClient):
    r = api_client.get("/openclaw", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers.get("location", "").endswith("/openclaw/")


def _connect_params(token: str) -> dict:
    return {
        "minProtocol": 3,
        "maxProtocol": 3,
        "client": {
            "id": "control-ui",
            "version": "test",
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


def test_websocket_handshake_hello_ok(api_client: TestClient, openclaw_token: str):
    with api_client.websocket_connect("/openclaw") as ws:
        first = ws.receive_json()
        assert first["type"] == "event"
        assert first["event"] == "connect.challenge"
        assert "nonce" in first.get("payload", {})

        ws.send_json(
            {
                "type": "req",
                "id": "c1",
                "method": "connect",
                "params": _connect_params(openclaw_token),
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == "res"
        assert msg["id"] == "c1"
        assert msg["ok"] is True
        payload = msg["payload"]
        assert payload["type"] == "hello-ok"
        assert payload["protocol"] == 3
        methods = payload["features"]["methods"]
        assert "chat.send" in methods
        assert "channels.status" in methods
        assert "sessions.usage" in methods


def test_websocket_channels_status_and_config(api_client: TestClient, openclaw_token: str):
    with api_client.websocket_connect("/openclaw") as ws:
        assert ws.receive_json()["event"] == "connect.challenge"
        ws.send_json(
            {"type": "req", "id": "c1", "method": "connect", "params": _connect_params(openclaw_token)}
        )
        assert ws.receive_json()["ok"] is True

        ws.send_json({"type": "req", "id": "r1", "method": "channels.status", "params": {}})
        m1 = ws.receive_json()
        assert m1["ok"] is True
        snap = m1["payload"]
        assert "whatsapp" in snap.get("channels", {})
        assert "channelOrder" in snap

        ws.send_json({"type": "req", "id": "r2", "method": "config.get", "params": {}})
        m2 = ws.receive_json()
        assert m2["ok"] is True
        cfg = m2["payload"]
        assert "raw" in cfg
        assert "_agent_os_runtime" in cfg.get("parsed", {})

        ws.send_json(
            {
                "type": "req",
                "id": "r3",
                "method": "sessions.usage",
                "params": {"startDate": "2026-01-01", "endDate": "2026-01-31", "limit": 100},
            }
        )
        m3 = ws.receive_json()
        assert m3["ok"] is True
        usage = m3["payload"]
        assert usage["startDate"] == "2026-01-01"
        assert usage["endDate"] == "2026-01-31"
        assert usage["sessions"] == []
        assert usage["totals"]["totalCost"] == 0
        assert usage["aggregates"]["messages"]["total"] == 0

        ws.send_json(
            {"type": "req", "id": "r4", "method": "sessions.usage.timeseries", "params": {"key": "main"}}
        )
        m4 = ws.receive_json()
        assert m4["ok"] is True
        assert m4["payload"]["sessionId"] == "main"
        assert m4["payload"]["points"] == []

        ws.send_json({"type": "req", "id": "r5", "method": "sessions.usage.logs", "params": {"key": "main"}})
        m5 = ws.receive_json()
        assert m5["ok"] is True
        assert m5["payload"]["logs"] == []


def test_tail_gateway_logs_empty_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("my_agent_os.enterprise.audit.audit_dir", lambda: tmp_path)
    from my_agent_os.api_gateway.openclaw_compat.snapshots import tail_gateway_logs

    out = tail_gateway_logs({"limit": 50})
    assert out["lines"]
    assert "No audit JSONL" in out["lines"][0] or "audit" in out["lines"][0].lower()


def test_tail_gateway_logs_with_file(tmp_path, monkeypatch: pytest.MonkeyPatch):
    adir = tmp_path
    adir.mkdir(exist_ok=True)
    f = adir / "audit_2099-01-01.jsonl"
    f.write_text('{"event":"route","ts":"x"}\n{"event":"tool_call"}\n', encoding="utf-8")
    monkeypatch.setattr("my_agent_os.enterprise.audit.audit_dir", lambda: adir)
    from my_agent_os.api_gateway.openclaw_compat.snapshots import tail_gateway_logs

    out = tail_gateway_logs({"limit": 10})
    assert out["file"] == str(f)
    assert len(out["lines"]) >= 1
    assert out["cursor"] == f.stat().st_size


@pytest.mark.asyncio
async def test_websocket_chat_send_mocked(api_client: TestClient, openclaw_token: str, monkeypatch):
    async def fake_route(*args, **kwargs):
        return {"answer": "mocked-reply"}

    import my_agent_os.api_gateway.openclaw_compat.gateway_ws as gws

    monkeypatch.setattr(gws, "route", fake_route)

    with api_client.websocket_connect("/openclaw") as ws:
        ws.receive_json()
        ws.send_json(
            {"type": "req", "id": "c1", "method": "connect", "params": _connect_params(openclaw_token)}
        )
        ws.receive_json()

        ws.send_json(
            {
                "type": "req",
                "id": "s1",
                "method": "chat.send",
                "params": {
                    "sessionKey": "main",
                    "message": "hello",
                    "idempotencyKey": "run-test-1",
                },
            }
        )
        ack = ws.receive_json()
        assert ack["ok"] is True
        assert ack["payload"].get("status") == "started"
        assert ack["payload"].get("runId") == "run-test-1"

        evt = ws.receive_json()
        assert evt["type"] == "event"
        assert evt["event"] == "chat"
        payload = evt["payload"]
        assert payload["state"] == "final"
        assert "mocked-reply" in json.dumps(payload)


def test_websocket_accepts_owner_api_key_when_no_dedicated_ws_token(
    monkeypatch: pytest.MonkeyPatch,
):
    """/openclaw WS auth.token may match API_KEY_OWNER when OPENCLAW_GATEWAY_TOKEN is unset."""
    import my_agent_os.config.settings as cfg

    unified = "pytest-unified-ws-via-owner-key"
    monkeypatch.setattr(cfg.settings, "DEV_DISABLE_TOKEN_AUTH", False)
    monkeypatch.setattr(cfg.settings, "OPENCLAW_GATEWAY_TOKEN", "")
    monkeypatch.setattr(cfg.settings, "API_KEY_OWNER", unified)

    import my_agent_os.api_gateway.openclaw_compat.gateway_ws as gw

    gw._session_histories.clear()
    try:
        from my_agent_os.api_gateway.main import app

        with TestClient(app) as client:
            with client.websocket_connect("/openclaw") as ws:
                assert ws.receive_json()["event"] == "connect.challenge"
                ws.send_json(
                    {
                        "type": "req",
                        "id": "c-u",
                        "method": "connect",
                        "params": _connect_params(unified),
                    }
                )
                msg = ws.receive_json()
                assert msg["ok"] is True
                assert msg["payload"]["type"] == "hello-ok"
    finally:
        gw._session_histories.clear()
