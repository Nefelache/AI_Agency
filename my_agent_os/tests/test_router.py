"""Smoke tests for the intent router engine."""

import pytest
from my_agent_os.agent_core.router_engine import route, _load_prompts, _build_system_message


def test_prompts_load_successfully():
    prompts = _load_prompts()
    assert "core_identity" in prompts
    assert "control_aesthetic" in prompts
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
