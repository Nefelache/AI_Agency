from __future__ import annotations

from datetime import date
from typing import List, Protocol

from src.core.models import Event


class BilibiliProvider(Protocol):
    def collect_for_date(self, d: date) -> List[Event]:
        """Collect Bilibili-related events for the provided date."""


class BrowserOnlyBilibiliProvider:
    """Simple provider that filters browser events to only keep Bilibili ones."""

    def __init__(self, browser_collector) -> None:
        self.browser_collector = browser_collector

    def collect_for_date(self, d: date) -> List[Event]:
        browser_events = self.browser_collector.collect_for_date(d)
        return [event for event in browser_events if event.url and "bilibili.com" in event.url]
