# core/agents/calendar_agent.py
"""CalendarAgent -- Google Calendar through the SAME shared OAuthConnector as EmailAgent.

* "what's my day look like" / "what's on tomorrow"   -> READ: list that day's events.
* "move my 2pm to 4pm" / "reschedule the board meeting to 3pm"
* "schedule lunch with Happy tomorrow at 1pm"         -> writes: always needs_confirmation,
                                                         because they affect other people's time.
* No Google account connected                         -> status "failed", pointer to Settings.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.agents.base_agent import AgentResult, BaseAgent, RiskTier

NO_ACCOUNT_SUMMARY = "No Google account connected yet — add it in Settings."
RECONNECT_SUMMARY = "Your Google account needs reconnecting in Settings to grant calendar access."
DEFAULT_DURATION = timedelta(hours=1)

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_ISO_DATE_RE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b")
_NUMERIC_DATE_RE = re.compile(r"\b(?P<d>\d{1,2})[-/](?P<m>\d{1,2})[-/](?P<y>\d{4})\b")
_MONTHNAME_DATE_RE = re.compile(
    r"\b(?P<d1>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon1>" + "|".join(_MONTHS) + r")\.?(?:\s*,?\s*(?P<y1>\d{4}))?\b"
    r"|\b(?P<mon2>" + "|".join(_MONTHS) + r")\.?\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(?P<y2>\d{4}))?\b",
    re.I,
)
_TIME_RE = re.compile(
    r"\b(?:(?P<noon>noon|midday)|(?P<midnight>midnight)"
    r"|(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?![a-z])"
    r"|(?P<h24>[01]?\d|2[0-3]):(?P<m24>\d{2}))",
    re.I,
)
_MOVE_RE = re.compile(r"\b(?:move|reschedule|push|shift|bump)\s+(?:my\s+|the\s+)?(?P<src>.+?)\s+(?:to|until|till)\s+(?P<dst>.+)$", re.I)
_CREATE_RE = re.compile(r"\b(?:schedule|book|set\s+up|put|add|create|mark|block)\s+(?:a\s+|an\s+)?(?P<rest>.+)$", re.I)
_DURATION_RE = re.compile(
    r"\bfor\s+(?:(?P<half>half\s+an?\s+hour)|(?P<an>an?\s+hour)|(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>minutes?|mins?|hours?|hrs?|h))\b",
    re.I,
)
_READ_HINT = re.compile(r"\b(what'?s|what\s+is|what\s+does|show|list|any|how'?s|do\s+i\s+have|my\s+day|agenda|calendar|schedule\s+(?:today|tomorrow|for))\b", re.I)


class CalendarAgent(BaseAgent):
    name = "calendar"
    default_tier = RiskTier.READ  # reads; every write is confirmed first
    route_keywords = (
        "calendar", "schedule", "meeting", "meetings", "agenda", "my day", "appointment", "appointments",
        "event", "events", "reschedule", "move my", "book", "free tomorrow", "free today",
    )
    route_overrides = ("system_ops", "local_research")

    # ── Dependencies (overridable / mockable) ───────────────────
    def _connector(self):
        from core.config import _read_yaml_config
        from modules.oauth_connector import google_connector_from_config
        return google_connector_from_config(_read_yaml_config())

    def _now(self) -> datetime:
        return datetime.now().astimezone()

    # ── Entry point ─────────────────────────────────────────────
    async def run(self, goal: str, context: dict) -> AgentResult:
        from modules.oauth_connector import (GOOGLE_CALENDAR_SCOPE, MissingScopeError, NotConnectedError,
                                             OAuthError)

        try:
            cal = await self._connector().get_client(service="calendar", version="v3",
                                                     require_scopes=[GOOGLE_CALENDAR_SCOPE])
        except NotConnectedError:
            return AgentResult(status="failed", summary=NO_ACCOUNT_SUMMARY)
        except MissingScopeError:
            return AgentResult(status="failed", summary=RECONNECT_SUMMARY)
        except OAuthError as exc:
            return AgentResult(status="failed", summary=f"Couldn't reach your Google account ({exc}).")

        text = (goal or "").strip()
        try:
            move = _MOVE_RE.search(text)
            if move:
                return await self._move(cal, move.group("src"), move.group("dst"))
            create = _CREATE_RE.search(text)
            if create and not _is_read_request(text):
                return await self._create(cal, create.group("rest"))
            return await self._read(cal, text)
        except Exception as exc:
            return AgentResult(status="failed", summary=f"Calendar request failed ({type(exc).__name__}).")

    # ── READ ────────────────────────────────────────────────────
    async def _read(self, cal, text: str) -> AgentResult:
        now = self._now()
        day = _parse_day(text, now) or now.date()
        events = await asyncio.to_thread(self._list_day, cal, day, now)
        label = _day_label(day, now)
        if not events:
            self.say(f"No events {label}")
            return AgentResult(status="done", summary=f"Nothing on your calendar {label}.", data=[])

        timed = [e for e in events if not e["all_day"]]
        self.say(f"{len(events)} event{'s' if len(events) != 1 else ''} {label}"
                 + (f"; first at {_fmt_time(timed[0]['start'])}" if timed else ""))
        self.hud({"type": "calendar_day", "date": day.isoformat(),
                  "events": [{k: e[k] for k in ("id", "summary", "all_day", "location")} |
                             {"start": e["start"].isoformat() if e["start"] else None} for e in events]})
        parts = [(f"all day: {e['summary']}" if e["all_day"] else f"{_fmt_time(e['start'])} {e['summary']}")
                 for e in events[:5]]
        more = f" and {len(events) - 5} more" if len(events) > 5 else ""
        summary = f"{len(events)} event{'s' if len(events) != 1 else ''} {label}: " + "; ".join(parts) + more
        return AgentResult(status="done", summary=summary, data=events)

    @staticmethod
    def _list_day(cal, day: date, now: datetime) -> List[Dict[str, Any]]:
        tz = now.tzinfo
        start = datetime.combine(day, time.min, tz)
        end = start + timedelta(days=1)
        res = cal.events().list(calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
                                singleEvents=True, orderBy="startTime", maxResults=50).execute()
        return [_normalise_event(e, tz) for e in res.get("items", []) if e.get("status") != "cancelled"]

    # ── MOVE (confirmation required) ────────────────────────────
    async def _move(self, cal, src: str, dst: str) -> AgentResult:
        now = self._now()
        src_day = _parse_day(src, now) or now.date()
        src_time = _parse_time(src)
        dst_time = _parse_time(dst)
        dst_day = _parse_day(dst, now) or src_day
        if dst_time is None:
            return AgentResult(status="failed", summary="What time should I move it to?")

        events = [e for e in await asyncio.to_thread(self._list_day, cal, src_day, now) if not e["all_day"]]
        label = _day_label(src_day, now)
        if src_time is not None:
            matches = [e for e in events if e["start"].time().replace(tzinfo=None) == src_time]
            what = f"at {_fmt_clock(src_time)} {label}"
        else:
            title = re.sub(r"\b(?:today'?s?|tomorrow'?s?|meeting|appointment|event|call)\b", " ", src, flags=re.I).strip()
            matches = [e for e in events if title and title.lower() in e["summary"].lower()] or \
                      [e for e in events if src.strip().lower() in e["summary"].lower()]
            what = f"matching '{src.strip()}' {label}"
        if not matches:
            return AgentResult(status="failed", summary=f"Nothing on your calendar {what}.")
        if len(matches) > 1:
            names = ", ".join(f"'{e['summary']}'" for e in matches[:3])
            return AgentResult(status="failed", summary=f"More than one event {what} ({names}); which one?")

        ev = matches[0]
        duration = (ev["end"] - ev["start"]) if ev["end"] else DEFAULT_DURATION
        new_start = datetime.combine(dst_day, dst_time, ev["start"].tzinfo)
        new_end = new_start + duration
        others = ev["attendee_count"]
        notify = f" ({others} other attendee{'s' if others != 1 else ''} will be notified)" if others else ""
        day_txt = "" if dst_day == src_day else f" {_day_label(dst_day, now)}"
        summary = f"Move '{ev['summary']}' from {_fmt_time(ev['start'])} to {_fmt_time(new_start)}{day_txt}{notify}?"
        return self.confirmation_required(
            summary, lambda: self._patch(cal, ev, new_start, new_end),
            event_id=ev["id"], new_start=new_start.isoformat(), new_end=new_end.isoformat(),
        )

    @staticmethod
    def _patch(cal, ev: Dict[str, Any], new_start: datetime, new_end: datetime) -> AgentResult:
        body = {"start": {"dateTime": new_start.isoformat()}, "end": {"dateTime": new_end.isoformat()}}
        if ev.get("time_zone"):
            body["start"]["timeZone"] = body["end"]["timeZone"] = ev["time_zone"]
        cal.events().patch(calendarId="primary", eventId=ev["id"], body=body, sendUpdates="all").execute()
        return AgentResult(status="done", summary=f"Moved '{ev['summary']}' to {_fmt_time(new_start)}")

    # ── CREATE (confirmation required) ──────────────────────────
    async def _create(self, cal, rest: str) -> AgentResult:
        now = self._now()
        start_time = _parse_time(rest)
        if start_time is None:
            return AgentResult(status="failed", summary="What time should I schedule it for?")
        day = _parse_day(rest, now) or now.date()
        duration = _parse_duration(rest) or DEFAULT_DURATION
        start = datetime.combine(day, start_time, now.tzinfo)
        if start < now and _parse_day(rest, now) is None:
            start += timedelta(days=1)  # "schedule X at 8am" said in the evening means tomorrow
        end = start + duration
        title = _extract_title(rest) or "Busy"
        summary = (f"Schedule '{title}' {_day_label(start.date(), now)} "
                   f"{_fmt_time(start)} to {_fmt_time(end)}?")
        return self.confirmation_required(
            summary, lambda: self._insert(cal, title, start, end),
            title=title, start=start.isoformat(), end=end.isoformat(),
        )

    @staticmethod
    def _insert(cal, title: str, start: datetime, end: datetime) -> AgentResult:
        body = {"summary": title, "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}
        created = cal.events().insert(calendarId="primary", body=body).execute()
        return AgentResult(status="done", summary=f"'{title}' is on your calendar for {_fmt_time(start)}",
                           data={"id": created.get("id")})


# ── parsing helpers ─────────────────────────────────────────────
def _is_read_request(text: str) -> bool:
    # "what's my schedule tomorrow" is a read, "schedule lunch tomorrow" is a write.
    return bool(re.match(r"^\s*(?:what|show|list|how|do i|any|tell me)\b", text, re.I)) or \
        bool(re.search(r"\bmy\s+schedule\b", text, re.I))


def _parse_time(text: str) -> Optional[time]:
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    if m.group("noon"):
        return time(12, 0)
    if m.group("midnight"):
        return time(0, 0)
    if m.group("h"):
        h, mins = int(m.group("h")), int(m.group("m") or 0)
        if not 1 <= h <= 12 or mins > 59:
            return None
        pm = m.group("ap").lower().startswith("p")
        h = (h % 12) + (12 if pm else 0)
        return time(h, mins)
    return time(int(m.group("h24")), int(m.group("m24")))


def _parse_explicit_date(text: str, now: datetime) -> Optional[date]:
    """An explicit calendar date mentioned by the user: '20-11-2026', '2026-11-20',
    '20th November', 'November 20 2026', etc. Tried before the relative-day words
    (today/tomorrow/weekday names) so an explicit date always wins."""
    t = text or ""
    m = _ISO_DATE_RE.search(t)
    if m:
        try:
            return date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        except ValueError:
            return None
    m = _NUMERIC_DATE_RE.search(t)
    if m:
        d, mo, y = int(m.group("d")), int(m.group("m")), int(m.group("y"))
        try:
            return date(y, mo, d)  # day-first (DD-MM-YYYY)
        except ValueError:
            try:
                return date(y, d, mo)  # fall back to MM-DD-YYYY if day-first is impossible
            except ValueError:
                return None
    m = _MONTHNAME_DATE_RE.search(t)
    if m:
        if m.group("mon1"):
            mon, day_s, year_s = m.group("mon1"), m.group("d1"), m.group("y1")
        else:
            mon, day_s, year_s = m.group("mon2"), m.group("d2"), m.group("y2")
        month = _MONTHS.get(mon.lower())
        if not month:
            return None
        try:
            result = date(int(year_s) if year_s else now.year, month, int(day_s))
        except ValueError:
            return None
        if not year_s and result < now.date():
            result = result.replace(year=result.year + 1)  # "Nov 20" said in December means next year
        return result
    return None


def _parse_day(text: str, now: datetime) -> Optional[date]:
    explicit = _parse_explicit_date(text, now)
    if explicit:
        return explicit
    t = (text or "").lower()
    today = now.date()
    if re.search(r"\btoday'?s?\b|\btonight\b|\bthis (?:morning|afternoon|evening)\b", t):
        return today
    if re.search(r"\btomorrow'?s?\b", t):
        return today + timedelta(days=1)
    for i, name in enumerate(_WEEKDAYS):
        if re.search(rf"\b{name}'?s?\b", t):
            ahead = (i - today.weekday()) % 7
            if ahead == 0 and re.search(rf"\bnext {name}\b", t):
                ahead = 7
            return today + timedelta(days=ahead)
    return None


def _parse_duration(text: str) -> Optional[timedelta]:
    m = _DURATION_RE.search(text or "")
    if not m:
        return None
    if m.group("half"):
        return timedelta(minutes=30)
    if m.group("an"):
        return timedelta(hours=1)
    n = float(m.group("n"))
    return timedelta(hours=n) if m.group("unit").lower().startswith("h") else timedelta(minutes=n)


def _extract_title(rest: str) -> str:
    t = _DURATION_RE.sub(" ", rest)
    t = re.sub(r"\b(?:at|from|on|for)?\s*" + _TIME_RE.pattern, " ", t, flags=re.I)
    t = _ISO_DATE_RE.sub(" ", t)
    t = _NUMERIC_DATE_RE.sub(" ", t)
    t = _MONTHNAME_DATE_RE.sub(" ", t)
    t = re.sub(r"\b(?:on\s+|this\s+|next\s+)?(?:today|tomorrow|tonight|" + "|".join(_WEEKDAYS) + r")\b", " ", t, flags=re.I)
    t = re.sub(r"\b(?:on|to|in)\s+(?:my\s+)?calendar\b", " ", t, flags=re.I)
    t = re.sub(r"^\s*as\s+", " ", t, flags=re.I)  # "mark <date> as meeting" -> drop the leading "as"
    t = re.sub(r"\s+(?:at|on|for|from|as)\s*$", "", re.sub(r"\s+", " ", t).strip(), flags=re.I)
    return t.strip(" ,.-")


def _normalise_event(e: Dict[str, Any], tz) -> Dict[str, Any]:
    s, en = e.get("start") or {}, e.get("end") or {}
    all_day = "date" in s and "dateTime" not in s
    start = _parse_dt(s.get("dateTime"), tz) if not all_day else None
    end = _parse_dt(en.get("dateTime"), tz) if not all_day else None
    attendees = [a for a in e.get("attendees", []) or [] if not a.get("self")]
    return {
        "id": e.get("id"),
        "summary": e.get("summary") or "(no title)",
        "start": start,
        "end": end,
        "all_day": all_day,
        "location": e.get("location", ""),
        "attendee_count": len(attendees),
        "time_zone": s.get("timeZone"),
    }


def _parse_dt(value: Optional[str], tz) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)


def _fmt_clock(t: time) -> str:
    return datetime.combine(date.today(), t).strftime("%I:%M %p").lstrip("0")


def _fmt_time(dt: datetime) -> str:
    return _fmt_clock(dt.timetz().replace(tzinfo=None))


def _day_label(day: date, now: datetime) -> str:
    if day == now.date():
        return "today"
    if day == now.date() + timedelta(days=1):
        return "tomorrow"
    return f"on {day.strftime('%A %d %B').replace(' 0', ' ')}"
