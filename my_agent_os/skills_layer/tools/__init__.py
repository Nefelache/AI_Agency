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
    """Return metadata for all registered tools (router injects into system prompt)."""
    _ensure_loaded()
    return [
        {
            "name": name,
            "description": getattr(cls, "description", ""),
            "skill_instructions": getattr(cls, "skill_instructions", "") or "",
        }
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
    """Import every *.py module in this package exactly once, then load external SKILL.md plugins."""
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

    # 加载外部 SKILL.md 插件（~/.coreclaw/skills/）
    _load_external_skill_packs()


def _load_external_skill_packs() -> None:
    """从 ~/.coreclaw/skills/ 扫描并注册外部 SKILL.md 技能包。"""
    try:
        from my_agent_os.config.local_config import get_local_config
        from my_agent_os.skills_layer.skill_loader import discover_external_skills

        skills_dir = get_local_config().skills_dir
        packs = discover_external_skills(skills_dir)
        for skill in packs:
            if skill.name not in _registry:
                _registry[skill.name] = type(skill)
                logger.info("外部技能已注册: %s", skill.name)
    except Exception as exc:
        logger.debug("外部 SKILL.md 插件加载跳过: %s", exc)


# Eagerly load on import so that skills are always available
_ensure_loaded()
