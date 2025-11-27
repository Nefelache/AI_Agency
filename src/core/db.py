from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import List

from .models import Event


class Database:
    """Simple SQLite wrapper for storing Event records."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                type TEXT NOT NULL,
                ts_start TEXT NOT NULL,
                ts_end TEXT,
                duration_sec INTEGER,
                title TEXT,
                url TEXT,
                meta TEXT
            )
            """
        )
        self.conn.commit()

    def insert_event(self, event: Event) -> None:
        payload = (
            event.source,
            event.type,
            event.ts_start.isoformat(),
            event.ts_end.isoformat() if event.ts_end else None,
            event.duration_sec,
            event.title,
            event.url,
            json.dumps(event.meta) if event.meta is not None else None,
        )
        self.conn.execute(
            """
            INSERT INTO events (
                source, type, ts_start, ts_end, duration_sec, title, url, meta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        self.conn.commit()

    def get_events_by_date(self, d: date) -> List[Event]:
        start_dt = datetime.combine(d, time.min)
        end_dt = datetime.combine(d, time.max)
        cursor = self.conn.execute(
            """
            SELECT source, type, ts_start, ts_end, duration_sec, title, url, meta
            FROM events
            WHERE ts_start BETWEEN ? AND ?
            ORDER BY ts_start ASC
            """,
            (start_dt.isoformat(), end_dt.isoformat()),
        )
        rows = cursor.fetchall()
        events: List[Event] = []
        for row in rows:
            meta = json.loads(row["meta"]) if row["meta"] else None
            events.append(
                Event(
                    source=row["source"],
                    type=row["type"],
                    ts_start=datetime.fromisoformat(row["ts_start"]),
                    ts_end=datetime.fromisoformat(row["ts_end"]) if row["ts_end"] else None,
                    duration_sec=row["duration_sec"],
                    title=row["title"],
                    url=row["url"],
                    meta=meta,
                )
            )
        return events
