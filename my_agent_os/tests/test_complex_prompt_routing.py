"""
Regression / behavior tests for very long user prompts (e.g. full Cursor spec paste).

Console + registered crew: complexity pre-check + 3-phase crew => many LLM calls
and large payloads — explains long wall times or apparent "hangs" behind the API.
"""

from __future__ import annotations

import json

import pytest

from my_agent_os.agent_core.crew.orchestrator import CrewOrchestrator


@pytest.mark.asyncio
async def test_console_with_crew_uses_ten_llm_calls_for_complex_task(monkeypatch):
    """1 complexity + 4 phase1 + 4 phase2 + 1 synthesis = 10 (4 dept profiles)."""
    from my_agent_os.agent_core import router_engine as re

    calls: list[str] = []

    async def fake_llm(system_message: str, user_message: str, response_json: bool = False, **_kw):
        if response_json and "complexity evaluator" in system_message:
            calls.append("complexity")
            return json.dumps({"score": 5, "reason": "multi-agent build spec"})
        if "Analyze this task from your department" in user_message:
            calls.append("phase1")
            return "Dept analysis."
        if "FINAL position" in user_message:
            calls.append("phase2")
            return "Final position."
        if "Synthesize into your final recommendation" in user_message:
            calls.append("phase3")
            return "Executive recommendation."
        calls.append("unexpected")
        return json.dumps({"answer": "unexpected", "next_actions": []})

    monkeypatch.setattr(re, "call_llm", fake_llm)
    re.set_memory_engine(None)
    re.set_crew_orchestrator(CrewOrchestrator(llm=fake_llm))

    long_input = (
        "从零搭建 LangGraph 主管 + Zep 记忆 + 企微 Webhook + SQLite ERP；"
        "含四个子 Agent 与完整目录树。" * 80
    )

    try:
        result = await re.route(
            long_input,
            channel="console",
            user_id="test-complex-prompt",
            with_memory=False,
        )
    finally:
        re.set_crew_orchestrator(None)

    assert result.get("answer") or result.get("recommendation")
    assert len(calls) == 10, f"expected 10 LLM invocations, got {len(calls)}: {calls}"
    assert calls.count("complexity") == 1
    assert calls.count("phase1") == 4
    assert calls.count("phase2") == 4
    assert calls.count("phase3") == 1


@pytest.mark.asyncio
async def test_console_without_crew_single_llm_even_for_long_input(monkeypatch):
    """When crew is not registered (e.g. unit context), no complexity pre-flight."""
    from my_agent_os.agent_core import router_engine as re

    calls = 0

    async def fake_llm(system_message: str, user_message: str, **_kw):
        nonlocal calls
        calls += 1
        return json.dumps(
            {
                "answer": "Single-path reply.",
                "next_actions": [],
            }
        )

    monkeypatch.setattr(re, "call_llm", fake_llm)
    re.set_memory_engine(None)
    re.set_crew_orchestrator(None)

    long_input = "Very long spec " * 500
    await re.route(long_input, channel="console", user_id="test-no-crew", with_memory=False)
    assert calls == 1


@pytest.mark.asyncio
async def test_mobile_long_input_single_llm(monkeypatch):
    from my_agent_os.agent_core import router_engine as re

    calls = 0

    async def fake_llm(system_message: str, user_message: str, **_kw):
        nonlocal calls
        calls += 1
        return json.dumps(
            {
                "action": "respond",
                "brief": "ok",
                "options": ["A"],
            }
        )

    monkeypatch.setattr(re, "call_llm", fake_llm)
    re.set_crew_orchestrator(CrewOrchestrator(llm=fake_llm))

    try:
        long_input = "Marketing automation " * 400
        await re.route(long_input, channel="mobile", user_id="test-m", with_memory=False)
    finally:
        re.set_crew_orchestrator(None)

    assert calls == 1
