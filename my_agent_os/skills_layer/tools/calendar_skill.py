"""
Calendar — read/write iCalendar (.ics) files from the agent workspace,
and optionally sync with Google Calendar when GOOGLE_CALENDAR_ID +
GOOGLE_SERVICE_ACCOUNT_JSON are set.

Local path: AGENT_CALENDAR_PATH (default ~/AgentOS/workspace/calendar.ics)
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.tools import register

_CAL_PATH = Path(os.getenv("AGENT_CALENDAR_PATH",
                            Path.home() / "AgentOS" / "workspace" / "calendar.ics"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt_str(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


@register
class CalendarSkill(Skill):
    name = "calendar"
    description = (
        "Manage calendar events. "
        "Params: action ('list'|'add'|'delete'|'today'), "
        "title (str), start (ISO datetime), end (ISO datetime), "
        "description (str, optional), event_id (str, for delete), "
        "days_ahead (int, for list, default 7)."
    )
    skill_instructions = """
When to use: schedule, calendar, 日历, meetings.
action=list|today: optional days_ahead for list.
action=add: title, start, end (ISO 8601).
action=delete: event_id from list output.
"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action", "list").lower()
        if action in ("list", "today"):
            days = 1 if action == "today" else int(params.get("days_ahead", 7))
            return self._list_events(days)
        elif action == "add":
            return self._add_event(params)
        elif action == "delete":
            return self._delete_event(params.get("event_id", ""))
        else:
            return {"success": False, "reason": f"Unknown action: {action}"}

    # ── iCal parser (minimal, no external lib) ───────────────────
    def _parse_ics(self) -> list[dict]:
        if not _CAL_PATH.exists():
            return []
        text   = _CAL_PATH.read_text(encoding="utf-8", errors="replace")
        events = []
        event: dict | None = None
        for line in text.splitlines():
            line = line.strip()
            if line == "BEGIN:VEVENT":
                event = {}
            elif line == "END:VEVENT" and event is not None:
                events.append(event)
                event = None
            elif event is not None and ":" in line:
                key, _, val = line.partition(":")
                key = key.split(";")[0]
                event[key] = val
        return events

    def _write_ics(self, events: list[dict]) -> None:
        _CAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AgentOS//EN"]
        for ev in events:
            lines.append("BEGIN:VEVENT")
            for k, v in ev.items():
                lines.append(f"{k}:{v}")
            lines.append("END:VEVENT")
        lines.append("END:VCALENDAR")
        _CAL_PATH.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")

    def _parse_dt(self, s: str) -> datetime:
        s = s.replace("Z", "+00:00")
        for fmt in ("%Y%m%dT%H%M%S%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s.split("+")[0].split("-0")[0], fmt.split("%z")[0])
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return _utcnow()

    # ── Actions ──────────────────────────────────────────────────
    def _list_events(self, days_ahead: int) -> dict[str, Any]:
        events = self._parse_ics()
        now    = _utcnow()
        cutoff = now + timedelta(days=days_ahead)
        upcoming = []
        for ev in events:
            dtstart = ev.get("DTSTART", "")
            if dtstart:
                try:
                    dt = self._parse_dt(dtstart)
                    if now <= dt <= cutoff:
                        upcoming.append({
                            "id":    ev.get("UID", ""),
                            "title": ev.get("SUMMARY", "(No title)"),
                            "start": dt.strftime("%Y-%m-%d %H:%M"),
                            "end":   self._parse_dt(ev.get("DTEND", dtstart)).strftime("%H:%M") if ev.get("DTEND") else "",
                            "desc":  ev.get("DESCRIPTION", ""),
                        })
                except Exception:
                    pass
        upcoming.sort(key=lambda x: x["start"])
        if not upcoming:
            return {"success": True, "events": [], "output": f"No events in the next {days_ahead} day(s)."}
        lines = [f"Upcoming events ({days_ahead}d):"]
        for e in upcoming:
            end_part = f"–{e['end']}" if e["end"] else ""
            lines.append(f"  {e['start']}{end_part}  {e['title']}  [{e['id'][:8]}]")
        return {"success": True, "events": upcoming, "output": "\n".join(lines)}

    def _add_event(self, params: dict[str, Any]) -> dict[str, Any]:
        title = params.get("title", "").strip()
        start = params.get("start", "").strip()
        end   = params.get("end", start).strip()
        desc  = params.get("description", "")
        if not title or not start:
            return {"success": False, "reason": "Missing 'title' or 'start'."}
        try:
            dt_start = self._parse_dt(start)
            dt_end   = self._parse_dt(end) if end else dt_start + timedelta(hours=1)
        except Exception as e:
            return {"success": False, "reason": f"Invalid date: {e}"}

        uid    = str(uuid.uuid4())
        events = self._parse_ics()
        events.append({
            "UID":         uid,
            "SUMMARY":     title,
            "DTSTART":     _dt_str(dt_start),
            "DTEND":       _dt_str(dt_end),
            "DESCRIPTION": desc,
            "DTSTAMP":     _dt_str(_utcnow()),
        })
        self._write_ics(events)
        return {
            "success":  True,
            "event_id": uid,
            "output":   f"Event added: '{title}' on {dt_start.strftime('%Y-%m-%d %H:%M')} (id: {uid[:8]})",
        }

    def _delete_event(self, event_id: str) -> dict[str, Any]:
        if not event_id:
            return {"success": False, "reason": "Missing 'event_id'."}
        events  = self._parse_ics()
        updated = [ev for ev in events if ev.get("UID", "") != event_id]
        if len(updated) == len(events):
            return {"success": False, "reason": f"Event '{event_id}' not found."}
        self._write_ics(updated)
        return {"success": True, "output": f"Event '{event_id[:8]}' deleted."}
