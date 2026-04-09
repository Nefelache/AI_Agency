"""Tests for the hot-pluggable skill registry."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from my_agent_os.skills_layer.tools import get_tool, list_tools
from my_agent_os.skills_layer.tools.web_search import WebSearch


def test_email_handler_registered():
    names = [t["name"] for t in list_tools()]
    assert "email" in names


def test_all_skills_registered():
    """All expected skills should appear in the registry."""
    names = [t["name"] for t in list_tools()]
    expected = {"email", "web_search", "weather", "http_fetch",
                "file_manager", "code_runner", "calendar", "reminder", "notion"}
    assert expected.issubset(set(names)), f"Missing skills: {expected - set(names)}"


@patch("my_agent_os.skills_layer.tools.email_handler.smtplib.SMTP")
def test_email_handler_execute(mock_smtp):
    mock_smtp.return_value.__enter__.return_value = MagicMock()
    with patch.multiple(
        "my_agent_os.skills_layer.tools.email_handler",
        _SMTP_HOST="localhost",
        _SMTP_USER="user",
        _SMTP_PASS="pass",
        _FROM_ADDR="user@example.com",
    ):
        tool = get_tool("email")
        result = asyncio.run(tool.execute({"to": "ceo@acme.com", "subject": "Q3 Report", "body": "Attached."}))
    assert result["success"] is True


def test_email_handler_missing_fields():
    tool = get_tool("email")
    result = asyncio.run(tool.execute({"body": "no recipient"}))
    assert result["success"] is False


def test_code_runner_execute():
    tool = get_tool("code_runner")
    result = asyncio.run(tool.execute({"code": "print('hello')", "language": "python"}))
    assert result["success"] is True
    assert "hello" in result["output"]


def test_code_runner_missing_code():
    tool = get_tool("code_runner")
    result = asyncio.run(tool.execute({}))
    assert result["success"] is False


def test_file_manager_missing_path():
    tool = get_tool("file_manager")
    result = asyncio.run(tool.execute({"action": "read"}))
    assert result["success"] is False


@pytest.mark.asyncio
async def test_web_search_missing_query():
    tool = get_tool("web_search")
    result = await tool.execute({})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_reminder_set_and_cancel():
    tool = get_tool("reminder")
    set_result = await tool.execute({"action": "set", "message": "test reminder", "in_seconds": 3600})
    assert set_result["success"] is True
    rid = set_result["reminder_id"]
    cancel_result = await tool.execute({"action": "cancel", "reminder_id": rid})
    assert cancel_result["success"] is True


@pytest.mark.asyncio
async def test_web_search_fallback_to_second_provider(monkeypatch: pytest.MonkeyPatch):
    tool = WebSearch()
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    monkeypatch.setenv("SERPAPI_KEY", "y")
    monkeypatch.setenv("WEB_SEARCH_ALLOW_DDG", "1")

    async def fake_tavily(*args, **kwargs):
        return {"ok": False, "code": "PROVIDER_TIMEOUT", "message": "timeout", "provider": "tavily"}

    async def fake_serpapi(*args, **kwargs):
        return {"ok": True, "code": "OK", "message": "ok", "output": "hit", "provider": "serpapi"}

    monkeypatch.setattr(tool, "_tavily_search", fake_tavily)
    monkeypatch.setattr(tool, "_serpapi_search", fake_serpapi)
    out = await tool.execute({"query": "test query"})
    assert out["ok"] is True
    assert out["provider"] == "serpapi"
    assert out.get("fallbacks")
    assert out["fallbacks"][0]["provider"] == "tavily"


@pytest.mark.asyncio
async def test_web_search_returns_last_error_with_fallbacks(monkeypatch: pytest.MonkeyPatch):
    tool = WebSearch()
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setenv("SERPAPI_KEY", "")
    monkeypatch.setenv("WEB_SEARCH_ALLOW_DDG", "1")

    async def fake_ddg(*args, **kwargs):
        return {"ok": False, "code": "EMPTY_RESULT", "message": "none", "provider": "duckduckgo"}

    monkeypatch.setattr(tool, "_ddg_search", fake_ddg)
    out = await tool.execute({"query": "very niche keyword"})
    assert out["ok"] is False
    assert out["code"] == "EMPTY_RESULT"
    assert "fallbacks" in out["data"]
