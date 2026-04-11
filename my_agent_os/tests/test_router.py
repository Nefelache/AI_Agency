"""Smoke tests for the intent router engine."""

import pytest
from my_agent_os.agent_core.router_engine import (
    _build_system_message,
    _load_prompts,
    _try_direct_skill_route,
    route,
)


def test_prompts_load_successfully():
    prompts = _load_prompts()
    assert "core_identity" in prompts
    assert "control_aesthetic" in prompts
    assert "memory_grounding" in prompts
    assert "decision_engine" in prompts
    assert "preferences" in prompts


def test_system_message_includes_channel_override():
    prompts = _load_prompts()
    msg = _build_system_message(prompts, "mobile")
    assert "MOBILE" in msg

    msg_console = _build_system_message(prompts, "console")
    assert "DESKTOP CONSOLE" in msg_console


def test_preferences_enforce_red_lines():
    prompts = _load_prompts()
    prefs = prompts["preferences"]
    assert "Rap" in prefs["music"]["blacklist"]
    assert "Alcohol" in prefs["health"]["allergies"]
    assert prefs["interaction"]["anxiety_dampening"] is True


@pytest.mark.asyncio
async def test_mobile_route_returns_options():
    result = await route(raw_input="approve the vendor quote", channel="mobile", user_id="test")
    assert "options" in result
    assert isinstance(result["options"], list)


@pytest.mark.asyncio
async def test_console_route_returns_next_actions():
    result = await route(raw_input="summarize Q3 contracts", channel="console", user_id="test")
    assert "next_actions" in result


def test_direct_route_detects_chinese_search_intent():
    out = _try_direct_skill_route("帮我查一下今天英伟达新闻", {"web_search"})
    assert out is not None
    skill, params = out
    assert skill == "web_search"
    assert "英伟达新闻" in params["query"]


def test_direct_route_detects_english_search_intent():
    out = _try_direct_skill_route("look up latest deepseek release notes", {"web_search"})
    assert out is not None
    skill, params = out
    assert skill == "web_search"
    assert "latest deepseek release notes" in params["query"]
