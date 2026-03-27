"""
Skills Tool Registry.

Auto-discovers all Skill subclasses in this package and exposes them
via `get_tool(name)` and `list_tools()` for the router engine to dispatch.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from my_agent_os.skills_layer.base import Skill

logger = logging.getLogger(__name__)

_registry: dict[str, type["Skill"]] = {}


def register(cls: type["Skill"]) -> type["Skill"]:
    """Class decorator: drops a Skill into the global registry."""
    _registry[cls.name] = cls
    return cls


def get_tool(name: str) -> "Skill":
    """Instantiate a tool by name. Stateless — fresh instance each call."""
    _ensure_loaded()
    if name not in _registry:
        raise KeyError(f"Unknown tool: {name!r}. Available: {list(_registry)}")
    return _registry[name]()


def list_tools() -> list[dict]:
    """Return a list of {name, description} dicts for all registered tools."""
    _ensure_loaded()
    return [
        {"name": name, "description": getattr(cls, "description", "")}
        for name, cls in sorted(_registry.items())
    ]


def reload_tool(name: str) -> None:
    """Force re-import a skill module by stem name (used by skill_writer after writing a new file)."""
    module_name = f"my_agent_os.skills_layer.tools.{name}"
    if module_name in sys.modules:
        importlib.reload(sys.modules[module_name])
    else:
        importlib.import_module(module_name)
    logger.info("Reloaded skill module: %s", module_name)


_loaded = False


def _ensure_loaded() -> None:
    """Import every *.py module in this package exactly once."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    pkg = "my_agent_os.skills_layer.tools"
    for path in sorted(Path(__file__).parent.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        module_name = f"{pkg}.{path.stem}"
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            logger.warning("Skill auto-load failed for %s: %s", module_name, exc)


# Eagerly load on import so that skills are always available
_ensure_loaded()
