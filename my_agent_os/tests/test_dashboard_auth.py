"""Dashboard login (JWT) + RBAC: employee vs root."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def auth_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import my_agent_os.auth.user_store as us_mod
    import my_agent_os.config.settings as cfg

    db = tmp_path / "u.db"
    us_mod._store = None
    monkeypatch.setattr(cfg.settings, "USERS_DB_PATH", str(db))
    monkeypatch.setattr(cfg.settings, "JWT_SECRET", "pytest-jwt-secret-fixed")
    monkeypatch.setattr(cfg.settings, "API_KEY_OWNER", "")
    monkeypatch.setattr(cfg.settings, "API_KEY_CHANNEL", "")
    monkeypatch.setattr(cfg.settings, "API_KEY_GUEST", "")

    store = us_mod.get_user_store()
    store.create_user("emp@test.dev", "password12", role="employee")
    store.create_user("root@test.dev", "password12", role="root")

    from my_agent_os.api_gateway.main import app

    with TestClient(app) as tc:
        yield tc


def _login(client: TestClient, email: str) -> str:
    r = client.post("/auth/login", json={"email": email, "password": "password12"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_login_and_me(auth_client: TestClient):
    tok = _login(auth_client, "emp@test.dev")
    r = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "employee"
    s = auth_client.get("/auth/session", headers={"Authorization": f"Bearer {tok}"})
    assert s.json()["role"] == "employee"


def test_employee_console_ok(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    async def fake_route(*args, **kwargs):
        return {"answer": "ok"}

    import my_agent_os.agent_core.router_engine as re

    monkeypatch.setattr(re, "route", fake_route)

    tok = _login(auth_client, "emp@test.dev")
    r = auth_client.post(
        "/console/query",
        headers={"Authorization": f"Bearer {tok}"},
        json={"query": "hi", "include_memory": False},
    )
    assert r.status_code == 200
    assert r.json().get("answer") == "ok"


def test_employee_cannot_seal(auth_client: TestClient):
    tok = _login(auth_client, "emp@test.dev")
    r = auth_client.post("/memory/seal", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_root_can_seal(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from my_agent_os.api_gateway.routes import memory_api

    eng = memory_api._engine
    assert eng is not None

    async def fake_seal(uid: str):
        return {"status": "sealed", "topic": "t", "summary": "s"}

    monkeypatch.setattr(eng, "force_seal_session", fake_seal)

    tok = _login(auth_client, "root@test.dev")
    r = auth_client.post("/memory/seal", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200


def test_api_key_still_root(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import my_agent_os.config.settings as cfg

    monkeypatch.setattr(cfg.settings, "API_KEY_OWNER", "k-root-test")

    r = auth_client.post("/memory/seal", headers={"X-API-Key": "k-root-test"})
    # engine may return no session — still authorized
    assert r.status_code in (200, 500)
