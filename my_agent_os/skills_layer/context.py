"""
Skills Layer Context — shared references injected at app startup.

Follows the same module-level singleton pattern used by router_engine.py.
Set during lifespan in api_gateway/main.py; consumed by any skill that needs
direct memory access (matrix_planner, render_worker, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from my_agent_os.memory_layer.engine import MemoryEngine

_memory_engine: "MemoryEngine | None" = None


def set_memory_engine(engine: "MemoryEngine") -> None:
    global _memory_engine
    _memory_engine = engine


def get_memory_engine() -> "MemoryEngine":
    if _memory_engine is None:
        raise RuntimeError(
            "MemoryEngine not initialised in skills context. "
            "Ensure skills_layer.context.set_memory_engine() is called during app lifespan."
        )
    return _memory_engine
