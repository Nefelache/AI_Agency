from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List

from src.collectors.base import Collector
from src.core.models import Event


class ChromeHistoryCollector(Collector):
    source_name = "browser"

    def collect_for_date(self, d: date) -> List[Event]:
        base_dt = datetime.combine(d, time(hour=9))
        events: List[Event] = []

        study_event = Event(
            source=self.source_name,
            type="page_view",
            ts_start=base_dt,
            ts_end=base_dt + timedelta(minutes=45),
            duration_sec=45 * 60,
            title="GRE Study Guide",
            url="https://docs.example.com/gre-study",
            meta={"category": "study", "browser": "chrome"},
        )
        events.append(study_event)

        entertainment_start = base_dt + timedelta(hours=2)
        entertainment_event = Event(
            source=self.source_name,
            type="page_view",
            ts_start=entertainment_start,
            ts_end=entertainment_start + timedelta(minutes=30),
            duration_sec=30 * 60,
            title="Relaxing Bilibili Video",
            url="https://www.bilibili.com/video/xyz",
            meta={"category": "entertainment", "browser": "chrome"},
        )
        events.append(entertainment_event)

        quick_check_start = entertainment_start + timedelta(hours=1)
        quick_check_event = Event(
            source=self.source_name,
            type="page_view",
            ts_start=quick_check_start,
            ts_end=quick_check_start + timedelta(minutes=5),
            duration_sec=5 * 60,
            title="Productivity Blog",
            url="https://productivity.example.com/focus",
            meta={"category": "study", "browser": "chrome"},
        )
        events.append(quick_check_event)

        return events
