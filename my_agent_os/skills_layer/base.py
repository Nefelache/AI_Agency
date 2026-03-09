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

    @abstractmethod
    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run the skill. Must not hold state between invocations."""
        ...
