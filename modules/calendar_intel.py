# modules/calendar_intel.py
"""
Calendar & Scheduling Intel Module for J.A.R.V.I.S..
Provides event management, double-booking conflict detection, auto-rescheduling,
and J.A.R.V.I.S. persona reminders ("Hey, dumbass, you got a meeting in 5").
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

CALENDAR_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "calendar_data.json")


class CalendarIntelManager:
    def __init__(self, filepath: str = CALENDAR_FILE):
        self.filepath = filepath
        self._ensure_storage()

    def _ensure_storage(self):
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        if not os.path.exists(self.filepath):
            now = datetime.now()
            initial_events = [
                {
                    "id": "evt_1",
                    "title": "Team Strategy Briefing",
                    "start": (now + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M"),
                    "end": (now + timedelta(minutes=65)).strftime("%Y-%m-%d %H:%M"),
                    "location": "War Room / Zoom",
                    "attendees": ["boss@company.com", "partner"],
                    "urgent": True
                },
                {
                    "id": "evt_2",
                    "title": "Security Code Review",
                    "start": (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M"),
                    "end": (now + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M"),
                    "location": "HQ",
                    "attendees": ["devs@company.com"]
                },
                {
                    "id": "evt_3",
                    "title": "Overlapping Client Sync",
                    "start": (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M"),
                    "end": (now + timedelta(hours=2, minutes=30)).strftime("%Y-%m-%d %H:%M"),
                    "location": "Phone",
                    "attendees": ["client@external.org"]
                }
            ]
            self._save(initial_events)

    def _load(self) -> List[Dict[str, Any]]:
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, events: List[Dict[str, Any]]):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(events, f, indent=2)
        except Exception as e:
            print(f"[calendar_intel] Error saving calendar: {e}")

    def get_upcoming_events(self, limit: int = 5) -> List[Dict[str, Any]]:
        events = self._load()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        upcoming = [e for e in events if e.get("start", "") >= now_str]
        upcoming.sort(key=lambda x: x.get("start", ""))
        return upcoming[:limit]

    def check_conflicts(self) -> List[Dict[str, Any]]:
        """Identify overlapping calendar events."""
        events = self._load()
        conflicts = []
        fmt = "%Y-%m-%d %H:%M"

        parsed = []
        for e in events:
            try:
                st = datetime.strptime(e["start"], fmt)
                et = datetime.strptime(e["end"], fmt)
                parsed.append((st, et, e))
            except Exception:
                continue

        parsed.sort(key=lambda x: x[0])
        for i in range(len(parsed)):
            for j in range(i + 1, len(parsed)):
                st1, et1, e1 = parsed[i]
                st2, et2, e2 = parsed[j]
                if st2 < et1:
                    conflicts.append({"event_a": e1, "event_b": e2})

        return conflicts

    def add_event(self, title: str, start_time: str, end_time: str, location: str = "TBD") -> str:
        events = self._load()
        evt_id = f"evt_{int(time.time())}"
        new_evt = {
            "id": evt_id,
            "title": title,
            "start": start_time,
            "end": end_time,
            "location": location,
            "urgent": False
        }
        events.append(new_evt)
        self._save(events)
        return f"Added calendar event '{title}' ({start_time} - {end_time})."

    def reschedule_event(self, event_query: str, new_start: str, new_end: str) -> str:
        events = self._load()
        found = False
        query_lower = event_query.lower()
        for e in events:
            if query_lower in e.get("title", "").lower() or query_lower in e.get("id", "").lower():
                e["start"] = new_start
                e["end"] = new_end
                found = True
                break

        if found:
            self._save(events)
            return f"Rescheduled meeting '{event_query}' to {new_start} - {new_end}."
        return f"Could not find event matching '{event_query}' to reschedule."

    def format_jarvis_reminders(self) -> str:
        """Format calendar alerts in authentic J.A.R.V.I.S. voice."""
        upcoming = self.get_upcoming_events(limit=3)
        conflicts = self.check_conflicts()
        now = datetime.now()
        fmt = "%Y-%m-%d %H:%M"

        lines = []
        imminent = []

        for e in upcoming:
            try:
                st = datetime.strptime(e["start"], fmt)
                diff_mins = int((st - now).total_seconds() / 60)
                if 0 <= diff_mins <= 30:
                    imminent.append((e, diff_mins))
            except Exception:
                continue

        if imminent:
            for e, mins in imminent:
                if mins <= 5:
                    lines.append(f"Hey, dumbass, you got '{e['title']}' in {mins} minutes at {e.get('location', 'TBD')}! Get moving!")
                else:
                    lines.append(f"Heads up, partner: '{e['title']}' is starting in {mins} minutes.")

        if conflicts:
            c = conflicts[0]
            lines.append(
                f"You double-booked your dumb ass at {c['event_a']['start']}! "
                f"'{c['event_a']['title']}' overlaps with '{c['event_b']['title']}'. Want me to auto-reschedule one?"
            )

        if not lines and upcoming:
            next_evt = upcoming[0]
            lines.append(f"Next workflow on your plate is '{next_evt['title']}' at {next_evt['start']}. Clear skies for now.")

        if not lines:
            lines.append("Calendar is completely clear right now, buddy. No meetings to blow off.")

        return " ".join(lines)
