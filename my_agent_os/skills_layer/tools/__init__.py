"""
Skills Tool Registry.

Auto-discovers all Skill subclasses in this package and exposes them
via `get_tool(name)` for the router engine to dispatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from my_agent_os.skills_layer.base import Skill

_registry: dict[str, type["Skill"]] = {}


def register(cls: type["Skill"]) -> type["Skill"]:
    """Class decorator: drops a Skill into the global registry."""
    _registry[cls.name] = cls
    return cls


def get_tool(name: str) -> "Skill":
    """Instantiate a tool by name. Stateless — fresh instance each call."""
    if name not in _registry:
        raise KeyError(f"Unknown tool: {name}. Available: {list(_registry)}")
    return _registry[name]()


def list_tools() -> list[str]:
    return list(_registry)
