from __future__ import annotations

from datetime import date
from typing import Any, Dict

from src.core.config import DB_PATH
from src.core.db import Database


def build_daily_summary(d: date) -> Dict[str, Any]:
    db = Database(DB_PATH)
    events = db.get_events_by_date(d)

    total_seconds = 0
    study_seconds = 0
    entertainment_seconds = 0
    bilibili_seconds = 0

    for event in events:
        duration = event.duration_sec or 0
        total_seconds += duration
        category = (event.meta or {}).get("category")
        if category == "study":
            study_seconds += duration
        if category == "entertainment":
            entertainment_seconds += duration
        if event.url and "bilibili.com" in event.url:
            bilibili_seconds += duration

    def minutes(seconds: int) -> int:
        return round(seconds / 60)

    summary: Dict[str, Any] = {
        "date": d.isoformat(),
        "total_minutes": minutes(total_seconds),
        "study_minutes": minutes(study_seconds),
        "entertainment_minutes": minutes(entertainment_seconds),
        "bilibili_minutes": minutes(bilibili_seconds),
    }
    return summary


def format_summary_text(summary: Dict[str, Any]) -> str:
    return (
        f"日期: {summary['date']}\n"
        f"总在线时长: {summary['total_minutes']} 分钟\n"
        f"学习: {summary['study_minutes']} 分钟\n"
        f"娱乐: {summary['entertainment_minutes']} 分钟 (其中 B站 {summary['bilibili_minutes']} 分钟)"
    )
