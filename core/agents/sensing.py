# core/agents/sensing.py
"""
The sensing half of autonomy: one route from every background sensor into the
supervisor, one snapshot of the operator's situation, and a feedback record of
how the tools are doing.

* ``emit_event``   -- sensors (AutonomousWatchdog, AmbientSentinel, SystemWatcher,
                      the email FollowUpChecker) hand what they noticed to the
                      supervisor's ``handle_event``. It returns True when the agent
                      framework is listening, and the sensor then does NOT speak
                      on its own (no double announcements). With no supervisor
                      running, sensors keep their old behaviour.
* ``WorldModel``   -- one snapshot assembled from signals that already existed in
                      separate places (idle time, quiet mode, battery, what JARVIS
                      is doing) plus the one that didn't: ``in_meeting``, read
                      from Google Calendar. The supervisor uses it to hold
                      routine announcements while the operator is in a meeting or
                      in quiet mode, and the loop sees it as context.
* ``Feedback``     -- every tool outcome (and every 'no' to a proposed action) is
                      written to self_upgrade's performance log, and read back:
                      tools that keep failing are flagged to the model.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════════════════════════
# Event route
# ═══════════════════════════════════════════════════════════════
_LISTENER: Dict[str, Any] = {"bus": None}
SEVERITIES = ("info", "warning", "critical")


def register_listener(event_bus) -> None:
    """Called by the supervisor once it subscribes to ``agent_event``."""
    _LISTENER["bus"] = event_bus


def unregister_listener(event_bus=None) -> None:
    if event_bus is None or _LISTENER["bus"] is event_bus:
        _LISTENER["bus"] = None


def agents_listening() -> bool:
    return _LISTENER["bus"] is not None


def emit_event(source: str, kind: str, summary: str, *, goal: str = "", data: Any = None,
               severity: str = "info", timely: bool = False, event_bus=None) -> bool:
    """Hand an observation to the agent framework. True = it will be dealt with
    (spoken, investigated, or held for later); the caller should not speak it."""
    bus = event_bus or _LISTENER["bus"]
    if bus is None:
        return False
    payload = {"source": source, "kind": kind, "summary": summary, "severity": severity if severity in SEVERITIES else "info"}
    if goal:
        payload["goal"] = goal
    if timely:
        payload["timely"] = True  # useless later (e.g. "your next meeting starts in 5 minutes"): never held back
    if data is not None:
        payload["data"] = data
    try:
        bus.emit("agent_event", payload)
        return True
    except Exception:
        return False


# Ambient Sentinel categories -> what the agents should do with them.
def sentinel_event(alert: Dict[str, Any]) -> Dict[str, Any]:
    cat = str(alert.get("category") or "")
    sev = {"critical": "critical", "warning": "warning"}.get(str(alert.get("severity")), "info")
    summary = str(alert.get("headline") or alert.get("message") or "Ambient alert")
    goal = ""
    if cat.startswith("calendar_"):
        goal = ("A meeting is about to start (see the event data). Use list_events for today to find it, then "
                "get_event_context for it, and give the operator a one-sentence heads-up with anything useful "
                "(a related email or file). Don't change anything.")
    elif cat.startswith("inbox_"):
        goal = "An urgent email arrived. Use check_inbox with urgent_only and tell the operator in one sentence who it's from and what it's about."
    elif cat in ("cpu_overload", "thermal_overload"):
        goal = "The computer is running hot or busy. Use cpu_and_processes and say in one sentence what's responsible."
    elif cat == "storage_low":
        goal = "Disk space is low. Use disk_usage and say what's filling it; mention they can ask you to clean temp files."
    return {"source": "ambient_sentinel", "kind": cat or "alert", "summary": summary, "severity": sev,
            "goal": goal, "timely": cat.startswith("calendar_"),
            "data": {"message": alert.get("message"), "headline": alert.get("headline")}}


def watchdog_event(alert: Dict[str, Any]) -> Dict[str, Any]:
    cat = str(alert.get("category") or "")
    title, text = str(alert.get("title") or ""), str(alert.get("text") or "")
    sev = "critical" if alert.get("urgency") == "critical" else ("warning" if cat == "hardware" else "info")
    goal = ""
    if cat == "hardware" and "battery" not in title.lower():
        goal = "A system resource is under pressure. Use memory_usage or cpu_and_processes and say in one sentence what's responsible."
    # aviation / seismic alerts are tactical telemetry: spoken as they are, not investigated here
    return {"source": "autonomous_watchdog", "kind": cat or "alert", "summary": text or title, "severity": sev,
            "goal": goal, "data": {"title": title}}


def catchup_event(hours_away: float, briefing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    away = f"{hours_away:.0f} hour{'s' if round(hours_away) != 1 else ''}" if hours_away < 48 else f"{hours_away / 24:.0f} days"
    goal = (f"The operator is back after about {away} away. Give a short catch-up in two or three sentences: "
            "check_inbox (anything urgent), list_events for today, and list_followups if any are due. Read only.")
    data = None
    if briefing:
        data = {"briefing": [f.get("snippet") for f in (briefing.get("structured_findings") or [])][:8]}
    return {"source": "autonomous_watchdog", "kind": "catchup", "severity": "info",
            "summary": f"Welcome back; you were away about {away}.", "goal": goal, "data": data}


# ═══════════════════════════════════════════════════════════════
# World model
# ═══════════════════════════════════════════════════════════════
@dataclass
class Snapshot:
    taken_at: float
    local_time: str
    idle_seconds: Optional[float] = None
    is_idle: Optional[bool] = None
    quiet_mode: bool = False
    in_meeting: Optional[bool] = None        # None = unknown (no calendar)
    meeting_ends: Optional[str] = None       # "15:30" (never the title: invite text is untrusted)
    next_meeting_in_min: Optional[int] = None
    battery_percent: Optional[int] = None
    on_battery: Optional[bool] = None
    running_tasks: int = 0
    pending_confirmations: int = 0
    deferred_events: int = 0
    weak_tools: List[str] = field(default_factory=list)

    def busy(self) -> bool:
        """Should routine announcements wait?"""
        return bool(self.quiet_mode or self.in_meeting)

    def context_lines(self) -> List[str]:
        bits = [f"Operator's local time: {self.local_time}."]
        if self.in_meeting:
            bits.append(f"The operator is in a meeting until {self.meeting_ends or 'later'}: keep answers very short.")
        elif self.next_meeting_in_min is not None and self.next_meeting_in_min <= 30:
            bits.append(f"Their next meeting starts in {self.next_meeting_in_min} minutes.")
        if self.quiet_mode:
            bits.append("Quiet mode is on: only say what is needed.")
        if self.on_battery and self.battery_percent is not None and self.battery_percent <= 20:
            bits.append(f"The laptop is on battery at {self.battery_percent}%.")
        if self.weak_tools:
            bits.append("Recently unreliable tools (double-check their results, prefer asking the operator): "
                        + ", ".join(self.weak_tools) + ".")
        return [" ".join(bits)]


class WorldModel:
    """Assembles a Snapshot. Calendar state is cached (it costs a network call)."""

    meeting_ttl_s = 300.0

    def __init__(self, supervisor=None):
        self.supervisor = supervisor
        self._meeting: Dict[str, Any] = {"at": 0.0, "value": None}
        self._lock = threading.Lock()

    async def snapshot(self, *, refresh_calendar: bool = True) -> Snapshot:
        now = time.time()
        tz = _operator_tz()
        snap = Snapshot(taken_at=now, local_time=datetime.now(timezone.utc).astimezone(tz).strftime("%A %d %B, %H:%M"))
        wd = _watchdog()
        if wd is not None:
            try:
                snap.idle_seconds = round(wd.get_idle_seconds(), 1)
                snap.is_idle = bool(wd.is_idle())
            except Exception:
                pass
        snap.quiet_mode = _quiet_mode()
        try:
            import psutil
            bat = psutil.sensors_battery()
            if bat is not None:
                snap.battery_percent, snap.on_battery = int(bat.percent), not bat.power_plugged
        except Exception:
            pass
        sup = self.supervisor
        if sup is not None:
            snap.running_tasks = len([t for t in sup.active if not t.startswith("group-")])
            snap.pending_confirmations = len(sup.pending)
            snap.deferred_events = len(getattr(sup, "deferred_events", []))
        snap.weak_tools = Feedback.weak_tools()
        meeting = await self._meeting_state(refresh_calendar)
        if meeting:
            snap.in_meeting, snap.meeting_ends, snap.next_meeting_in_min = meeting
        return snap

    async def _meeting_state(self, refresh: bool):
        cached = self._meeting
        if cached["value"] is not None and (time.time() - cached["at"] < self.meeting_ttl_s or not refresh):
            return cached["value"]
        if not refresh:
            return None
        value = None
        try:
            value = await asyncio.wait_for(_calendar_meeting_state(), 8.0)
        except Exception:
            value = None
        if value is not None:
            self._meeting = {"at": time.time(), "value": value}
        return value

    def invalidate(self) -> None:
        self._meeting = {"at": 0.0, "value": None}


async def _calendar_meeting_state():
    """(in_meeting, ends 'HH:MM', minutes to next) from Google Calendar, or None if unavailable."""
    from core.agents.calendar_agent import CalendarAgent
    from core.agents.supervisor import agent_state_dir
    if agent_state_dir() is None:
        return None  # test runs never touch the real account

    class _NullBus:
        def emit(self, *a, **k):
            pass

    agent = CalendarAgent(_NullBus(), "world-model", name="calendar")
    cal = await agent._cal()
    tz, _ = await agent._tz(cal)
    now = datetime.now(timezone.utc).astimezone(tz)
    events = await agent._events_between(cal, tz, now - timedelta(hours=12), now + timedelta(hours=12))
    current = [e for e in events if not e["all_day"] and not e["transparent"] and e["start"] <= now
               and (e["end"] or e["start"] + timedelta(hours=1)) > now]
    upcoming = sorted(e["start"] for e in events if not e["all_day"] and e["start"] > now)
    ends = max((e["end"] or e["start"] + timedelta(hours=1)) for e in current) if current else None
    nxt = int((upcoming[0] - now).total_seconds() // 60) if upcoming else None
    return bool(current), (ends.strftime("%H:%M") if ends else None), nxt


def _operator_tz():
    try:
        from core.agents.calendar_agent import _known_tz_name, _zone
        tz = _zone(_known_tz_name())
        if tz is not None:
            return tz
    except Exception:
        pass
    return datetime.now().astimezone().tzinfo


def _loaded_instance(module: str, cls: str):
    """A sensor's singleton if it is running. Only looks at modules already imported:
    importing them just to read a flag is slow, and a sensor that was never
    imported can't be running."""
    import sys
    mod = sys.modules.get(module)
    return getattr(getattr(mod, cls, None), "_instance", None) if mod is not None else None


def _watchdog():
    return _loaded_instance("modules.autonomous_watchdog", "AutonomousWatchdog")


def _quiet_mode() -> bool:
    inst = _loaded_instance("modules.ambient_sentinel", "AmbientSentinel")
    return bool(inst is not None and getattr(inst, "quiet_mode", False))


# ═══════════════════════════════════════════════════════════════
# Feedback: tool outcomes -> self_upgrade's performance log -> back to the loop
# ═══════════════════════════════════════════════════════════════
class Feedback:
    PREFIX = "agent:"
    MIN_RUNS = 5
    WEAK_BELOW = 70.0
    _memory: Dict[str, Dict[str, float]] = {}

    @classmethod
    def _store(cls):
        from core.agents.supervisor import ROOT, agent_state_dir
        state = agent_state_dir()
        if state is None or state.resolve() != (ROOT / "data" / "agents").resolve():
            # tests, or a run with a redirected state folder (JARVIS_AGENT_STATE_DIR):
            # never touch the real data/performance_feedback.json
            return None
        try:
            from modules.self_upgrade import JarvisSelfUpgrade
            return JarvisSelfUpgrade()
        except Exception:
            return None

    @classmethod
    def record(cls, tool: str, ok: bool, note: str = "") -> None:
        key = cls.PREFIX + tool
        store = cls._store()
        if store is not None:
            try:
                store.record_skill_feedback(key, ok, note[:200])
                return
            except Exception:
                pass
        stats = cls._memory.setdefault(key, {"hits": 0, "whiffs": 0, "accuracy": 100.0})
        stats["hits" if ok else "whiffs"] += 1
        total = stats["hits"] + stats["whiffs"]
        stats["accuracy"] = round(stats["hits"] / total * 100, 1)

    @classmethod
    def stats(cls) -> Dict[str, Dict[str, float]]:
        store = cls._store()
        if store is not None:
            try:
                return {k: v for k, v in (store._load_logs().get("skill_stats") or {}).items() if k.startswith(cls.PREFIX)}
            except Exception:
                return {}
        return dict(cls._memory)

    @classmethod
    def weak_tools(cls) -> List[str]:
        out = []
        for key, s in cls.stats().items():
            runs = int(s.get("hits", 0)) + int(s.get("whiffs", 0))
            if runs >= cls.MIN_RUNS and float(s.get("accuracy", 100)) < cls.WEAK_BELOW:
                out.append(f"{key[len(cls.PREFIX):]} ({float(s['accuracy']):.0f}% over {runs})")
        return sorted(out)
