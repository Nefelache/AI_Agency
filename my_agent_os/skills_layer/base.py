"""
Skill Base Class — the DNA of every 'Limb'.

Rules:
  - All skills MUST be stateless.
  - All skills MUST return a dict.
  - Registration happens via the @register decorator in tools/__init__.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class Skill(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    # OpenClaw-style: full invocation rules for the main LLM (params, when to call, examples).
    skill_instructions: ClassVar[str] = ""

    @abstractmethod
    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run the skill. Must not hold state between invocations."""
        ...
