from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import List

from src.core.models import Event


class Collector(ABC):
    source_name: str

    @abstractmethod
    def collect_for_date(self, d: date) -> List[Event]:
        """Collect events for the provided date."""

