from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.watch_history import WatchHistory
from app.services.classifier import classify


def convert_item(item: dict) -> WatchHistory:
    history = item.get("history") or {}
    bvid = history.get("bvid") or item.get("bvid") or ""
    view_ts = item.get("view_at") or history.get("view_at")
    if not view_ts:
        raise ValueError("缺少 view_at 字段，无法写入 watch_history")
    view_at = datetime.fromtimestamp(view_ts)
    # Bilibili history uses `progress` as watch progress; `-1` usually means
    # finished. For business reporting we prefer the full video duration when
    # available, and fall back to progress only when it is a positive value.
    raw_progress = item.get("progress")
    raw_duration = item.get("duration")
    if isinstance(raw_duration, (int, float)) and raw_duration > 0:
        duration = int(raw_duration)
    elif isinstance(raw_progress, (int, float)) and raw_progress > 0:
        duration = int(raw_progress)
    else:
        duration = 0
    title = item.get("title") or history.get("title") or "未知标题"
    author = item.get("author_name") or item.get("author")
    tname = item.get("tname") or item.get("typename")
    category = classify(tname, title)

    return WatchHistory(
        bvid=bvid,
        title=title,
        author=author,
        view_at=view_at,
        date=view_at.date(),
        duration=int(duration),
        tname=tname,
        category=category,
        source="bilibili",
    )


def store_history_items(session: Session, items: Iterable[dict], *, return_details: bool = False):
    inserted = 0
    skipped = 0
    for item in items:
        try:
            record = convert_item(item)
        except ValueError:
            continue
        existing = (
            session.query(WatchHistory)
            .filter(WatchHistory.bvid == record.bvid, WatchHistory.view_at == record.view_at)
            .one_or_none()
        )
        if existing:
            existing.duration = record.duration
            existing.title = record.title
            existing.category = record.category
            existing.tname = record.tname
            skipped += 1
        else:
            session.add(record)
            inserted += 1
    session.commit()
    if return_details:
        return {"inserted": inserted, "skipped": skipped}
    return inserted
