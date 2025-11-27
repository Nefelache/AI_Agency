from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Dict, Iterable, List

from app.models.watch_history import WatchHistory


def build_daily_stats(records: Iterable[WatchHistory]) -> dict:
    records = sorted(records, key=lambda r: r.view_at)
    total_seconds = sum(r.duration for r in records)
    per_category = defaultdict(int)
    for r in records:
        per_category[r.category or "other"] += r.duration

    sessions = _build_sessions(records)
    deep_session_seconds = sum(s["duration"] for s in sessions if s["duration"] >= 20 * 60)
    fragmented_session_seconds = sum(s["duration"] for s in sessions if s["duration"] < 10 * 60)

    per_category_list = [
        {"name": name, "seconds": seconds, "ratio": (seconds / total_seconds) if total_seconds else 0}
        for name, seconds in per_category.items()
    ]

    return {
        "total_seconds": total_seconds,
        "video_count": len(records),
        "per_category": per_category_list,
        "deep_session_minutes": round(deep_session_seconds / 60),
        "fragmented_session_minutes": round(fragmented_session_seconds / 60),
        "videos": [
            {
                "bvid": r.bvid,
                "title": r.title,
                "author": r.author,
                "duration": r.duration,
                "view_at": r.view_at.isoformat(),
                "category": r.category,
            }
            for r in records
        ],
    }


def _build_sessions(records: List[WatchHistory]) -> List[dict]:
    sessions: List[dict] = []
    current: dict | None = None
    last_time = None

    for r in records:
        if current is None:
            current = {"start": r.view_at, "end": r.view_at, "duration": r.duration or 0}
            last_time = r.view_at
            continue

        if last_time and (r.view_at - last_time) <= timedelta(minutes=10):
            current["end"] = r.view_at
            current["duration"] += r.duration or 0
        else:
            sessions.append(current)
            current = {"start": r.view_at, "end": r.view_at, "duration": r.duration or 0}
        last_time = r.view_at

    if current:
        sessions.append(current)

    return sessions


def build_range_stats(records: Iterable[WatchHistory]) -> dict:
    records = sorted(records, key=lambda r: r.view_at)
    total_seconds = sum(r.duration for r in records)
    per_category_seconds: Dict[str, int] = defaultdict(int)
    per_category_counts: Dict[str, int] = defaultdict(int)
    unique_days = set()

    for r in records:
        cat = r.category or "other"
        per_category_seconds[cat] += r.duration
        per_category_counts[cat] += 1
        unique_days.add(r.date)

    sessions = _build_sessions(records)
    deep = sum(s["duration"] for s in sessions if s["duration"] >= 20 * 60)
    mid = sum(s["duration"] for s in sessions if 10 * 60 <= s["duration"] < 20 * 60)
    fragmented = sum(s["duration"] for s in sessions if s["duration"] < 10 * 60)

    category_breakdown = {
        name: {
            "minutes": round(seconds / 60),
            "video_count": per_category_counts[name],
        }
        for name, seconds in per_category_seconds.items()
    }

    return {
        "total_seconds": total_seconds,
        "video_count": len(records),
        "deep_minutes": round(deep / 60),
        "mid_minutes": round(mid / 60),
        "fragmented_minutes": round(fragmented / 60),
        "covered_days": len(unique_days),
        "by_category": category_breakdown,
    }
