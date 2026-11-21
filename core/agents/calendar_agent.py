# core/agents/calendar_agent.py
"""CalendarAgent -- Google Calendar through the SAME shared OAuthConnector as EmailAgent,
exposed as tools for the supervisor's loop.

Tools (category / risk):
  read   list_events, find_free_slot, check_conflicts, get_event_context
  action create_event, move_event, delete_event   (DESTRUCTIVE: other people's time)

No date parsing here: the model sends ISO timestamps. They are read as wall-clock
times in the CALENDAR's own time zone (Google Calendar settings), never the
machine's. JARVIS often runs under WSL, whose clock is UTC: using the machine
zone put "1pm" at 6:30pm IST and moved late events onto the next day, so they
seemed not to exist. Every write is read back from Google afterwards and the
operator is told if it didn't land where intended.

Event titles, descriptions and locations come from whoever sent the invite, so
read results are marked untrusted (the loop fences them).
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Dict, List, Optional, Tuple

from core.agents.base_agent import AgentResult, BaseAgent, RiskTier, ToolSpec

NO_ACCOUNT_SUMMARY = "No Google account connected yet — add it in Settings."
RECONNECT_SUMMARY = "Your Google account needs reconnecting in Settings to grant calendar access."
NEEDS_MODEL_SUMMARY = ("Creating, moving and deleting events need the planning model (Groq), which isn't available "
                       "right now. I can still read today's or tomorrow's calendar.")
DEFAULT_DURATION = timedelta(hours=1)
WORK_START, WORK_END = time(9, 0), time(18, 0)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ISO_HINT = "Local wall-clock time in the operator's calendar time zone, 'YYYY-MM-DDTHH:MM' (no offset)"


def _zone(name: Optional[str]) -> Optional[tzinfo]:
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def parse_local(value: Any, tz: tzinfo) -> Optional[datetime]:
    """An ISO date/time from the model, as wall-clock time in ``tz``.

    Any offset the model added is dropped: models routinely append 'Z' to a time
    the operator said in local terms, which is exactly the UTC/IST bug. A
    different zone must be asked for explicitly with a ``time_zone`` argument.
    """
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"(?:Z|[+-]\d{2}:?\d{2})$", "", text.replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2} \d", text) else text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.combine(date.fromisoformat(text), time(0, 0))
        except ValueError:
            return None
    return dt.replace(tzinfo=tz)


def _calendar_errors(fn):
    async def wrapper(self, args: dict, ctx: dict) -> AgentResult:
        from modules.oauth_connector import MissingScopeError, NotConnectedError, OAuthError
        try:
            return await fn(self, args or {}, ctx or {})
        except NotConnectedError:
            return AgentResult(status="failed", summary=NO_ACCOUNT_SUMMARY)
        except MissingScopeError:
            return AgentResult(status="failed", summary=RECONNECT_SUMMARY)
        except OAuthError as exc:
            return AgentResult(status="failed", summary=f"Couldn't reach your Google account ({exc}).")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return AgentResult(status="failed", summary=f"Calendar request failed ({type(exc).__name__}).")
    wrapper.__name__ = fn.__name__
    return wrapper


class CalendarAgent(BaseAgent):
    name = "calendar"
    description = ("Google Calendar: what's on a day, free time, conflicts, schedule / move / delete events, and "
                   "everything related to a meeting (its email thread and files)")
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
        return datetime.now(timezone.utc)

    async def _cal(self):
        client = getattr(self, "_cal_client", None)
        if client is None:
            from modules.oauth_connector import GOOGLE_CALENDAR_SCOPE
            client = await self._connector().get_client(service="calendar", version="v3",
                                                        require_scopes=[GOOGLE_CALENDAR_SCOPE])
            self._cal_client = client
        return client

    async def _tz(self, cal) -> Tuple[tzinfo, str]:
        """The calendar's own time zone (Calendar settings), falling back to the machine's."""
        cached = getattr(self, "_tz_cache", None)
        if cached:
            return cached
        name = ""
        try:
            info = await asyncio.to_thread(lambda: cal.calendars().get(calendarId="primary").execute())
            name = str(info.get("timeZone") or "")
        except Exception:
            name = ""
        tz = _zone(name)
        if tz is None:
            name = _known_tz_name()  # last zone Google told us (survives restarts)
            tz = _zone(name)
        if tz is None:
            tz, name = datetime.now().astimezone().tzinfo, ""
        elif name != _known_tz_name():
            _remember_tz_name(name)
        self._tz_cache = (tz, name)
        return self._tz_cache

    # ── Catalogue ──────────────────────────────────────────────
    def tools(self) -> List[ToolSpec]:
        R, D = RiskTier.READ, RiskTier.DESTRUCTIVE

        def obj(props: dict, required: Tuple[str, ...] = ()) -> dict:
            return {"type": "object", "properties": props, "required": list(required)}

        when = {"type": "string", "description": _ISO_HINT}
        day = {"type": "string", "description": "Date 'YYYY-MM-DD' in the operator's calendar time zone"}
        return [
            ToolSpec("list_events", "List calendar events for a day (or several days), optionally matching a word. "
                                    "Returns event ids. To find an event the operator names without a date "
                                    "('the board meeting'), search from today with days=14 and query.",
                     obj({"date": day, "days": {"type": "integer", "description": "How many days (default 1, max 14)"},
                          "query": {"type": "string", "description": "Only events whose text contains this"}}, ("date",)),
                     "read", R, self.list_events, untrusted_output=True, progress="Checking your calendar"),
            ToolSpec("find_free_slot", "Find free time of a given length between working hours on a day (or the "
                                       "next few days).",
                     obj({"date": day, "duration_minutes": {"type": "integer"},
                          "earliest": {"type": "string", "description": "'HH:MM', default 09:00"},
                          "latest": {"type": "string", "description": "'HH:MM', default 18:00"},
                          "days": {"type": "integer", "description": "Look this many days ahead (default 1, max 7)"}},
                         ("date", "duration_minutes")),
                     "read", R, self.find_free_slot, progress="Looking for free time"),
            ToolSpec("check_conflicts", "Which events overlap a proposed time.",
                     obj({"start": when, "end": when}, ("start", "end")),
                     "read", R, self.check_conflicts, untrusted_output=True, progress="Checking for clashes"),
            ToolSpec("get_event_context", "Everything about one event at once: its details, the related email "
                                          "thread and related local files.",
                     obj({"event_id": {"type": "string"}}, ("event_id",)),
                     "read", R, self.get_event_context, untrusted_output=True, progress="Pulling the meeting together"),
            ToolSpec("create_event", "Create a calendar event (the operator approves first). Attendees get invites.",
                     obj({"title": {"type": "string"}, "start": when, "end": when,
                          "duration_minutes": {"type": "integer", "description": "Used when end is not given (default 60)"},
                          "attendees": {"type": "array", "items": {"type": "string"},
                                        "description": "Email addresses or names the operator gave"},
                          "location": {"type": "string"}, "description": {"type": "string"},
                          "time_zone": {"type": "string", "description": "Only if the operator named another zone, IANA name"}},
                         ("title", "start")),
                     "action", D, self.create_event, preview=self.create_preview, grounded_args=("attendees",),
                     progress="Adding it to your calendar"),
            ToolSpec("move_event", "Move an event (by id) to a new start; keeps its length unless new_end is given. "
                                   "The operator approves first; attendees are notified.",
                     obj({"event_id": {"type": "string"}, "new_start": when, "new_end": when}, ("event_id", "new_start")),
                     "action", D, self.move_event, preview=self.move_preview, progress="Moving the event"),
            ToolSpec("delete_event", "Delete or cancel an event by id. The operator approves first; attendees are notified.",
                     obj({"event_id": {"type": "string"}}, ("event_id",)),
                     "action", D, self.delete_event, preview=self.delete_preview, progress="Removing the event"),
        ]

    # ── Fallback entry point (no planning model) ───────────────
    async def run(self, goal: str, context: dict) -> AgentResult:
        """Only used when the tool loop can't run (no Groq): reading still works."""
        text = (goal or "").lower()
        if re.search(r"\b(?:move|reschedule|schedule\s+(?!for|today|tomorrow)|book|add|create|cancel|delete|push|shift)\b", text) \
                and not re.search(r"\b(?:what|show|list|any)\b", text):
            return AgentResult(status="failed", summary=NEEDS_MODEL_SUMMARY)
        return await self._fallback_read({"tomorrow": "tomorrow" in text}, {})

    @_calendar_errors
    async def _fallback_read(self, args: dict, ctx: dict) -> AgentResult:
        cal = await self._cal()
        tz, _ = await self._tz(cal)
        today = self._now().astimezone(tz).date()
        target = today + timedelta(days=1) if args.get("tomorrow") else today
        return await self.list_events({"date": target.isoformat()}, {})

    @classmethod
    def loop_context(cls) -> List[str]:
        """Tell the loop the operator's real 'now' (the machine clock may be UTC under WSL)."""
        name = _known_tz_name()
        tz = _zone(name)
        if tz is None:
            return []
        now = datetime.now(timezone.utc).astimezone(tz)
        return [f"The operator's time zone (from Google Calendar) is {name}; their local time is "
                f"{now.strftime('%A %d %B %Y, %H:%M')}. Use it for 'today', 'tomorrow' and all calendar times."]

    # ── Reading ────────────────────────────────────────────────
    def _list_range(self, cal, start: datetime, end: datetime, tz: tzinfo) -> List[Dict[str, Any]]:
        res = cal.events().list(calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
                                singleEvents=True, orderBy="startTime", maxResults=100).execute()
        return [_normalise_event(e, tz) for e in res.get("items", []) if e.get("status") != "cancelled"]

    async def _events_between(self, cal, tz, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._list_range, cal, start, end, tz)

    @_calendar_errors
    async def list_events(self, args: dict, ctx: dict) -> AgentResult:
        cal = await self._cal()
        tz, _ = await self._tz(cal)
        start = parse_local(args.get("date"), tz)
        if start is None:
            return AgentResult(status="failed", summary="list_events needs a date as YYYY-MM-DD.")
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        days = max(1, min(14, int(args.get("days") or 1)))
        events = await self._events_between(cal, tz, start, start + timedelta(days=days))
        query = str(args.get("query") or "").strip().lower()
        if query:
            events = [e for e in events if query in f"{e['summary']} {e['location']} {e['description']}".lower()]
        now = self._now().astimezone(tz)
        label = _day_label(start.date(), now) if days == 1 else f"in the {days} days from {_day_label(start.date(), now)}"
        if not events:
            self.say(f"No events {label}")
            return AgentResult(status="done", summary=f"Nothing on your calendar {label}.", data=[])
        timed = [e for e in events if not e["all_day"]]
        self.say(f"{len(events)} event{'s' if len(events) != 1 else ''} {label}"
                 + (f"; first at {_fmt_time(timed[0]['start'])}" if timed else ""))
        self.hud({"type": "calendar_day", "date": start.date().isoformat(),
                  "events": [{"id": e["id"], "summary": e["summary"], "all_day": e["all_day"], "location": e["location"],
                              "start": e["start"].isoformat() if e["start"] else None} for e in events]})
        parts = [(f"all day: {e['summary']}" if e["all_day"]
                  else f"{_fmt_when(e['start'], start.date() if days == 1 else None)} {e['summary']}") for e in events[:6]]
        more = f" and {len(events) - 6} more" if len(events) > 6 else ""
        return AgentResult(status="done", data=[_public(e) for e in events],
                           summary=f"{len(events)} event{'s' if len(events) != 1 else ''} {label}: " + "; ".join(parts) + more)

    @_calendar_errors
    async def find_free_slot(self, args: dict, ctx: dict) -> AgentResult:
        cal = await self._cal()
        tz, _ = await self._tz(cal)
        first = parse_local(args.get("date"), tz)
        minutes = int(args.get("duration_minutes") or 0)
        if first is None or minutes <= 0:
            return AgentResult(status="failed", summary="find_free_slot needs a date and a duration in minutes.")
        earliest = _clock(args.get("earliest")) or WORK_START
        latest = _clock(args.get("latest")) or WORK_END
        days = max(1, min(7, int(args.get("days") or 1)))
        now = self._now().astimezone(tz)
        need = timedelta(minutes=minutes)
        start_day = first.date()
        events = await self._events_between(cal, tz, datetime.combine(start_day, time(0), tz),
                                            datetime.combine(start_day + timedelta(days=days), time(0), tz))
        slots = []
        for offset in range(days):
            d = start_day + timedelta(days=offset)
            cursor = datetime.combine(d, earliest, tz)
            if d == now.date() and cursor < now:
                cursor = _round_up(now, 15)
            stop = datetime.combine(d, latest, tz)
            busy = sorted((e["start"], e["end"] or e["start"] + DEFAULT_DURATION) for e in events
                          if not e["all_day"] and e["start"].date() == d and not e["transparent"])
            for b_start, b_end in busy:
                if b_start - cursor >= need and cursor + need <= stop:
                    slots.append((cursor, b_start))
                cursor = max(cursor, b_end)
            if stop - cursor >= need:
                slots.append((cursor, stop))
            if len(slots) >= 3:
                break
        if not slots:
            return AgentResult(status="done", data=[],
                               summary=f"No free {minutes}-minute slot between {_fmt_clock(earliest)} and {_fmt_clock(latest)}.")
        data = [{"start": s.strftime("%Y-%m-%dT%H:%M"), "free_until": e.strftime("%Y-%m-%dT%H:%M")} for s, e in slots[:3]]
        text = "; ".join(f"{_day_label(s.date(), now)} from {_fmt_time(s)} (free until {_fmt_time(e)})" for s, e in slots[:3])
        return AgentResult(status="done", data=data, summary=f"Free for {minutes} minutes: {text}")

    @_calendar_errors
    async def check_conflicts(self, args: dict, ctx: dict) -> AgentResult:
        cal = await self._cal()
        tz, _ = await self._tz(cal)
        start, end = parse_local(args.get("start"), tz), parse_local(args.get("end"), tz)
        if start is None or end is None or end <= start:
            return AgentResult(status="failed", summary="check_conflicts needs a start before its end.")
        clashes = await self._clashes(cal, tz, start, end)
        if not clashes:
            return AgentResult(status="done", data=[], summary=f"Nothing else at {_fmt_time(start)}–{_fmt_time(end)}.")
        return AgentResult(status="done", data=[_public(e) for e in clashes],
                           summary="Clashes with " + "; ".join(f"'{e['summary']}' at {_fmt_time(e['start'])}" for e in clashes))

    async def _clashes(self, cal, tz, start: datetime, end: datetime, ignore_id: Optional[str] = None) -> List[dict]:
        events = await self._events_between(cal, tz, start - timedelta(hours=12), end + timedelta(hours=12))
        return [e for e in events if not e["all_day"] and e["id"] != ignore_id and not e["transparent"]
                and e["start"] < end and (e["end"] or e["start"] + DEFAULT_DURATION) > start]

    @_calendar_errors
    async def get_event_context(self, args: dict, ctx: dict) -> AgentResult:
        """One event, its email thread and its files -- fetched in parallel (they don't depend on each other)."""
        cal = await self._cal()
        tz, _ = await self._tz(cal)
        event = await self._get_event(cal, tz, str(args.get("event_id") or ""))
        if event is None:
            return AgentResult(status="failed", summary="There's no event with that id.")
        mail, files = await asyncio.gather(self._related_mail(event), self._related_files(event),
                                           return_exceptions=True)
        mail = [] if isinstance(mail, BaseException) else mail
        files = [] if isinstance(files, BaseException) else files
        bits = [f"'{event['summary']}' {_fmt_when(event['start'], None) if event['start'] else 'all day'}"]
        if event["attendees"]:
            bits.append(f"with {', '.join(event['attendees'][:4])}")
        bits.append(f"{len(mail)} related email{'s' if len(mail) != 1 else ''}")
        bits.append(f"{len(files)} related file{'s' if len(files) != 1 else ''}")
        return AgentResult(status="done", summary=", ".join(bits) + ".",
                           data={"event": _public(event), "emails": mail, "files": files})

    async def _get_event(self, cal, tz, event_id: str) -> Optional[dict]:
        if not event_id:
            return None
        try:
            raw = await asyncio.to_thread(lambda: cal.events().get(calendarId="primary", eventId=event_id).execute())
        except Exception:
            return None
        if not raw or raw.get("status") == "cancelled":
            return None
        return _normalise_event(raw, tz)

    async def _related_mail(self, event: dict) -> List[dict]:
        from core.agents.email_agent import EmailAgent
        email = EmailAgent(self.event_bus, self.task_id, name="email")
        gmail = await email._gmail()
        words = " ".join(w for w in re.findall(r"[A-Za-z0-9]{3,}", event["summary"])[:4])
        people = " OR ".join(f"from:{a}" for a in event["attendee_emails"][:3])
        query = " ".join(q for q in (f'"{words}"' if words else "", f"({people})" if people else "") if q) or "in:inbox"
        found = await asyncio.to_thread(email._list_messages, gmail, query + " newer_than:60d", 5)
        metas = await email._metas(gmail, [m["id"] for m in found])
        return [{"id": m["id"], "thread_id": m["thread_id"], "from": m["from_name"], "subject": m["subject"],
                 "snippet": m["snippet"][:200]} for m in metas]

    async def _related_files(self, event: dict) -> List[dict]:
        from modules import cloud_docs
        title = re.sub(r"\b(?:meeting|call|sync|catch[\s-]?up|with|and|the|re)\b", " ", event["summary"], flags=re.I)
        if not cloud_docs._tokenize(title.lower()):
            return []
        results = await asyncio.to_thread(lambda: cloud_docs.CloudDocumentManager().search_documents(title.strip(), limit=5))
        return [{"filename": r.get("filename"), "path": r.get("path"), "modified": r.get("modified")}
                for r in results if not r.get("is_dir")][:5]

    # ── Writing (each confirmed; each verified after) ──────────
    async def _attendee_addresses(self, names: List[str]) -> Tuple[List[str], Optional[AgentResult]]:
        out, pending = [], []
        for n in [str(x).strip() for x in names or [] if str(x).strip()]:
            (out if _EMAIL_RE.fullmatch(n) else pending).append(n.lower() if _EMAIL_RE.fullmatch(n) else n)
        if pending:
            from core.agents.email_agent import EmailAgent
            email = EmailAgent(self.event_bus, self.task_id, name="email")
            gmail = await email._gmail()
            for n in pending:
                hit, options = await email.resolve_contact(gmail, n)
                if hit is None:
                    opts = "; ".join(f"{d} <{a}>" if d else a for d, a in options[:3])
                    return [], AgentResult(status="failed", summary=(f"Which {n}? I found {opts}." if options else
                                                                     f"I don't know an email address for {n}.")
                                           + " Ask the operator.")
                out.append(hit[1])
        return out, None

    async def _create_plan(self, args: dict):
        cal = await self._cal()
        tz, tz_name = await self._tz(cal)
        zone = _zone(args.get("time_zone")) if args.get("time_zone") else None
        if args.get("time_zone") and zone is None:
            return None, AgentResult(status="failed", summary=f"I don't recognise the time zone '{args['time_zone']}'.")
        use_tz = zone or tz
        title = str(args.get("title") or "").strip()
        start = parse_local(args.get("start"), use_tz)
        if not title or start is None:
            return None, AgentResult(status="failed", summary="create_event needs a title and a start time.")
        end = parse_local(args.get("end"), use_tz)
        if end is None:
            end = start + timedelta(minutes=int(args.get("duration_minutes") or 60))
        if end <= start:
            return None, AgentResult(status="failed", summary="The event would end before it starts.")
        attendees, problem = await self._attendee_addresses(args.get("attendees") or [])
        if problem:
            return None, problem
        clashes = await self._clashes(cal, tz, start, end)
        return {"cal": cal, "tz": tz, "tz_name": args.get("time_zone") or tz_name, "title": title, "start": start,
                "end": end, "attendees": attendees, "clashes": clashes,
                "location": str(args.get("location") or ""), "description": str(args.get("description") or "")}, None

    @_calendar_errors
    async def create_preview(self, args: dict, ctx: dict) -> AgentResult:
        plan, problem = await self._create_plan(args)
        if problem:
            return problem
        now = self._now().astimezone(plan["tz"])
        start_local = plan["start"].astimezone(plan["tz"])
        text = (f"Schedule '{plan['title']}' {_day_label(start_local.date(), now)} "
                f"{_fmt_time(start_local)} to {_fmt_time(plan['end'].astimezone(plan['tz']))}")
        if plan["attendees"]:
            text += f", inviting {', '.join(plan['attendees'])}"
        if plan["clashes"]:
            text += f" (clashes with {', '.join(repr(e['summary']) for e in plan['clashes'][:2])})"
        return AgentResult(status="done", summary=text + "?",
                           data={"title": plan["title"], "start": plan["start"].isoformat(), "end": plan["end"].isoformat()})

    @_calendar_errors
    async def create_event(self, args: dict, ctx: dict) -> AgentResult:
        plan, problem = await self._create_plan(args)
        if problem:
            return problem
        cal = plan["cal"]
        body: Dict[str, Any] = {"summary": plan["title"], "start": _when_body(plan["start"], plan["tz_name"]),
                                "end": _when_body(plan["end"], plan["tz_name"])}
        if plan["attendees"]:
            body["attendees"] = [{"email": a} for a in plan["attendees"]]
        if plan["location"]:
            body["location"] = plan["location"]
        if plan["description"]:
            body["description"] = plan["description"]
        created = await asyncio.to_thread(lambda: cal.events().insert(
            calendarId="primary", body=body, sendUpdates="all" if plan["attendees"] else "none").execute())
        return await self._verify(cal, plan["tz"], created.get("id"), plan["start"],
                                  f"'{plan['title']}' is on your calendar for {_fmt_when(plan['start'].astimezone(plan['tz']), None)}")

    async def _move_plan(self, args: dict):
        cal = await self._cal()
        tz, tz_name = await self._tz(cal)
        event = await self._get_event(cal, tz, str(args.get("event_id") or ""))
        if event is None:
            return None, AgentResult(status="failed", summary="There's no event with that id; list the day's events first.")
        if event["all_day"]:
            return None, AgentResult(status="failed", summary="That's an all-day event; I can only move timed events.")
        new_start = parse_local(args.get("new_start"), tz)
        if new_start is None:
            return None, AgentResult(status="failed", summary="move_event needs new_start.")
        new_end = parse_local(args.get("new_end"), tz) or new_start + ((event["end"] - event["start"]) if event["end"] else DEFAULT_DURATION)
        clashes = await self._clashes(cal, tz, new_start, new_end, ignore_id=event["id"])
        return {"cal": cal, "tz": tz, "tz_name": event["time_zone"] or tz_name, "event": event,
                "start": new_start, "end": new_end, "clashes": clashes}, None

    @_calendar_errors
    async def move_preview(self, args: dict, ctx: dict) -> AgentResult:
        plan, problem = await self._move_plan(args)
        if problem:
            return problem
        ev, now = plan["event"], self._now().astimezone(plan["tz"])
        day_txt = "" if plan["start"].date() == ev["start"].date() else f" {_day_label(plan['start'].date(), now)}"
        others = ev["attendee_count"]
        notify = f" ({others} other attendee{'s' if others != 1 else ''} will be notified)" if others else ""
        clash = f" It would clash with {', '.join(repr(e['summary']) for e in plan['clashes'][:2])}." if plan["clashes"] else ""
        return AgentResult(status="done", data={"event_id": ev["id"], "new_start": plan["start"].isoformat(),
                                                "new_end": plan["end"].isoformat()},
                           summary=f"Move '{ev['summary']}' from {_fmt_time(ev['start'])} to {_fmt_time(plan['start'])}"
                                   f"{day_txt}{notify}?{clash}")

    @_calendar_errors
    async def move_event(self, args: dict, ctx: dict) -> AgentResult:
        plan, problem = await self._move_plan(args)
        if problem:
            return problem
        cal, ev = plan["cal"], plan["event"]
        body = {"start": _when_body(plan["start"], plan["tz_name"]), "end": _when_body(plan["end"], plan["tz_name"])}
        await asyncio.to_thread(lambda: cal.events().patch(calendarId="primary", eventId=ev["id"], body=body,
                                                           sendUpdates="all").execute())
        return await self._verify(cal, plan["tz"], ev["id"], plan["start"], f"Moved '{ev['summary']}' to {_fmt_time(plan['start'])}")

    @_calendar_errors
    async def delete_preview(self, args: dict, ctx: dict) -> AgentResult:
        cal = await self._cal()
        tz, _ = await self._tz(cal)
        ev = await self._get_event(cal, tz, str(args.get("event_id") or ""))
        if ev is None:
            return AgentResult(status="failed", summary="There's no event with that id; list the day's events first.")
        others = ev["attendee_count"]
        notify = f" ({others} other attendee{'s' if others != 1 else ''} will be told it's cancelled)" if others else ""
        when = "all day" if ev["all_day"] else _fmt_when(ev["start"], None)
        return AgentResult(status="done", data={"event_id": ev["id"]},
                           summary=f"Delete '{ev['summary']}' ({when}){notify}? This can't be undone.")

    @_calendar_errors
    async def delete_event(self, args: dict, ctx: dict) -> AgentResult:
        cal = await self._cal()
        tz, _ = await self._tz(cal)
        ev = await self._get_event(cal, tz, str(args.get("event_id") or ""))
        if ev is None:
            return AgentResult(status="failed", summary="That event is already gone.")
        await asyncio.to_thread(lambda: cal.events().delete(calendarId="primary", eventId=ev["id"],
                                                            sendUpdates="all").execute())
        return AgentResult(status="done", summary=f"Deleted '{ev['summary']}'", data={"event_id": ev["id"]})

    async def _verify(self, cal, tz, event_id: Optional[str], intended: datetime, ok_text: str) -> AgentResult:
        """Read the event back from Google: say so if it isn't where we meant it to be."""
        saved = await self._get_event(cal, tz, event_id or "")
        if saved is None:
            return AgentResult(status="failed", data={"event_id": event_id},
                               summary="Google accepted the change but I can't find the event afterwards; please check your calendar.")
        if saved["start"] is not None and abs((saved["start"] - intended).total_seconds()) > 60:
            return AgentResult(status="failed", data={"event_id": event_id},
                               summary=f"The event was saved, but at {_fmt_when(saved['start'], None)} instead of "
                                       f"{_fmt_when(intended.astimezone(tz), None)}. Please check it.")
        return AgentResult(status="done", summary=ok_text, data={"event_id": event_id, "start": saved["start"].isoformat()})


# ── helpers ─────────────────────────────────────────────────────
_TZ_MEMORY: Dict[str, str] = {}


def _known_tz_name() -> str:
    if "name" not in _TZ_MEMORY:
        from core.agents.supervisor import load_json_state
        _TZ_MEMORY["name"] = str((load_json_state("calendar_tz.json", {}) or {}).get("name") or "")
    return _TZ_MEMORY["name"]


def _remember_tz_name(name: str) -> None:
    from core.agents.supervisor import save_json_state
    _TZ_MEMORY["name"] = name
    save_json_state("calendar_tz.json", {"name": name})


def _when_body(dt: datetime, tz_name: str) -> Dict[str, str]:
    """Google wants an RFC3339 time; with the IANA zone too, recurring events and
    the Calendar UI keep it in the operator's zone."""
    body = {"dateTime": dt.isoformat()}
    if tz_name:
        body["timeZone"] = tz_name
    return body


def _normalise_event(e: Dict[str, Any], tz) -> Dict[str, Any]:
    s, en = e.get("start") or {}, e.get("end") or {}
    all_day = "date" in s and "dateTime" not in s
    start = _parse_dt(s.get("dateTime"), tz) if not all_day else None
    end = _parse_dt(en.get("dateTime"), tz) if not all_day else None
    others = [a for a in e.get("attendees", []) or [] if not a.get("self")]
    return {
        "id": e.get("id"),
        "summary": e.get("summary") or "(no title)",
        "start": start,
        "end": end,
        "all_day": all_day,
        "date": s.get("date"),
        "location": e.get("location", "") or "",
        "description": (e.get("description") or "")[:500],
        "attendee_count": len(others),
        "attendees": [a.get("displayName") or a.get("email", "") for a in others],
        "attendee_emails": [a.get("email", "") for a in others if a.get("email")],
        "time_zone": s.get("timeZone"),
        "transparent": e.get("transparency") == "transparent",
    }


def _public(e: dict) -> dict:
    """An event as data for the model (times as local ISO)."""
    return {"id": e["id"], "title": e["summary"], "all_day": e["all_day"],
            "start": e["start"].strftime("%Y-%m-%dT%H:%M") if e["start"] else e.get("date"),
            "end": e["end"].strftime("%Y-%m-%dT%H:%M") if e["end"] else None,
            "location": e["location"], "description": e["description"], "attendees": e["attendees"]}


def _parse_dt(value: Optional[str], tz) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)


def _clock(value: Any) -> Optional[time]:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(value or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return time(int(m.group(1)), int(m.group(2)))


def _round_up(dt: datetime, minutes: int) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    extra = (-dt.minute) % minutes
    return dt + timedelta(minutes=extra)


def _fmt_clock(t: time) -> str:
    return datetime.combine(date.today(), t).strftime("%I:%M %p").lstrip("0")


def _fmt_time(dt: datetime) -> str:
    return _fmt_clock(dt.timetz().replace(tzinfo=None))


def _fmt_when(dt: datetime, same_day: Optional[date]) -> str:
    if same_day is not None and dt.date() == same_day:
        return _fmt_time(dt)
    return f"{dt.strftime('%a %d %b').replace(' 0', ' ')} {_fmt_time(dt)}"


def _day_label(day: date, now: datetime) -> str:
    if day == now.date():
        return "today"
    if day == now.date() + timedelta(days=1):
        return "tomorrow"
    return f"on {day.strftime('%A %d %B').replace(' 0', ' ')}"
