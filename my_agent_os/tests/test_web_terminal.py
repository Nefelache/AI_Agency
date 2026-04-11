"""Console / web_terminal response shaping."""

from __future__ import annotations

from my_agent_os.api_gateway.routes.web_terminal import ConsoleResponse, _coerce_console_payload


def test_coerce_drops_non_dict_sources():
    out = _coerce_console_payload(
        {
            "answer": "ok",
            "sources": ["not-a-dict", 123],
            "next_actions": ["a", "b"],
            "crew_views": {"x": 1},
        }
    )
    assert out["sources"] is None
    assert out["next_actions"] == ["a", "b"]
    assert out["crew_views"] == {"x": "1"}
    ConsoleResponse(**out)


def test_coerce_keeps_dict_sources():
    out = _coerce_console_payload(
        {
            "answer": "ok",
            "sources": [{"title": "t", "url": "u"}],
            "next_actions": [],
        }
    )
    assert out["sources"] == [{"title": "t", "url": "u"}]
    ConsoleResponse(**out)
