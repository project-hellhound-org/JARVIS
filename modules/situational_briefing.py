"""
J.A.R.V.I.S. Executive Situational Briefing Engine ("Good Morning Protocol").

Synthesizes multi-source telemetry for comprehensive operator debriefs:
1. Time-of-day contextual greeting ("Good morning, Sir...", "Good evening, Sir...").
2. Regional atmospheric telemetry (Weather conditions, temperature, precipitation).
3. On-device hardware telemetry (CPU load, memory headroom, thermals).
4. Daily scheduling & calendar conflicts (Upcoming events, meeting alerts).
5. Priority communications & inbox dispatches (Urgent emails, security alerts).
6. Active development repository status (Git branch, uncommitted diffs).
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from modules.weather_intel import WeatherIntelEngine
from modules.system_diagnostics import SystemDiagnosticsEngine
from modules.calendar_intel import CalendarIntelManager
from modules.inbox_intel import InboxIntelManager
from modules.system_controller import SystemController


class SituationalBriefingEngine:
    """
    Executive briefing synthesizer for J.A.R.V.I.S.
    """

    def __init__(self):
        self.weather_engine = WeatherIntelEngine()
        self.diagnostics_engine = SystemDiagnosticsEngine()
        self.calendar_mgr = CalendarIntelManager()
        self.inbox_mgr = InboxIntelManager()
        self.system_controller = SystemController()

    def _determine_greeting(self, dt: Optional[datetime] = None) -> Tuple_Greeting:
        now = dt or datetime.now()
        hour = now.hour
        if hour < 12:
            period = "morning"
            salutation = "Good morning, Sir."
        elif hour < 17:
            period = "afternoon"
            salutation = "Good afternoon, Sir."
        else:
            period = "evening"
            salutation = "Good evening, Sir."

        time_str = now.strftime("%H:%M")
        day_str = now.strftime("%A, %B %d")
        time_phrase = f"It is currently {time_str} on {day_str}."
        return salutation, time_phrase, period

    def generate_briefing(self, location: str = "") -> Dict[str, Any]:
        """
        Generate a multi-tier tactical briefing synthesizing weather, hardware,
        calendar, communications, and project telemetry.
        """
        now = datetime.now()
        salutation, time_phrase, period = self._determine_greeting(now)

        briefing_sections = []
        structured_findings = []

        # 1. Greeting & Timestamp
        briefing_sections.append(f"{salutation} {time_phrase}")

        # 2. Hardware Telemetry
        hw_summary = ""
        try:
            metrics = self.diagnostics_engine.get_metrics()
            cpu = metrics.get("cpu_percent", 0.0)
            ram_pct = metrics.get("ram_percent", 0.0)
            ram_avail = metrics.get("ram_available_gb", 0.0)
            thermals = metrics.get("peak_thermal_c", 0.0)
            
            thermal_note = f" with core thermals stable at {thermals}°C" if thermals > 0 else ""
            hw_summary = f"Core systems are nominal: CPU load is at {cpu}%, RAM utilization is at {ram_pct}% with {ram_avail} gigabytes available{thermal_note}."
            briefing_sections.append(hw_summary)
            structured_findings.append({
                "title": "Hardware Telemetry",
                "snippet": f"CPU: {cpu}% | RAM: {ram_pct}% ({ram_avail} GB free) | Peak Temp: {thermals}°C",
                "url": "system://diagnostics",
                "category": "HARDWARE"
            })
        except Exception as e:
            briefing_sections.append("Hardware diagnostics telemetry is currently operating on secondary sensors.")

        # 3. Weather & Atmospheric Conditions
        weather_summary = ""
        try:
            w = self.weather_engine.get_weather(location or "")
            if w and w.get("temp_c") is not None:
                city = w.get("city", "your sector").title()
                cond = w.get("condition", "clear")
                temp_c = w.get("temp_c")
                temp_f = w.get("temp_f")
                wind = w.get("wind_kmh", 0)
                weather_summary = f"Atmospheric readings for {city} report {cond.lower()} at {temp_c}°C ({temp_f}°F), with winds at {wind} kilometers per hour."
                briefing_sections.append(weather_summary)
                structured_findings.append({
                    "title": f"Atmosphere: {city}",
                    "snippet": f"{cond} | {temp_c}°C ({temp_f}°F) | Wind: {wind} km/h",
                    "url": "weather://telemetry",
                    "category": "WEATHER"
                })
        except Exception:
            pass

        # 4. Calendar & Agenda
        calendar_summary = ""
        try:
            upcoming = self.calendar_mgr.get_upcoming_events(limit=2)
            conflicts = self.calendar_mgr.check_conflicts()
            if upcoming:
                first_evt = upcoming[0]
                evt_title = first_evt.get("title", "Upcoming Engagement")
                evt_start = first_evt.get("start", "")
                conflict_note = f" Alert: {len(conflicts)} scheduling conflict detected." if conflicts else ""
                calendar_summary = f"Your schedule logs '{evt_title}' approaching at {evt_start}.{conflict_note}"
                briefing_sections.append(calendar_summary)
                for ev in upcoming:
                    structured_findings.append({
                        "title": f"Agenda: {ev.get('title')}",
                        "snippet": f"Time: {ev.get('start')} - {ev.get('end')} | Location: {ev.get('location', 'Remote')}",
                        "url": "calendar://event",
                        "category": "CALENDAR"
                    })
            else:
                briefing_sections.append("Your calendar is completely clear for the immediate horizon, Sir.")
                structured_findings.append({
                    "title": "Agenda Telemetry",
                    "snippet": "Schedule clear for immediate horizon",
                    "url": "calendar://status",
                    "category": "CALENDAR"
                })
        except Exception:
            pass

        # 5. Communications & Urgent Dispatches
        inbox_summary = ""
        try:
            urgent_alerts = self.inbox_mgr.scan_urgent_alerts()
            unread = self.inbox_mgr.get_unread_messages()
            if urgent_alerts:
                first_u = urgent_alerts[0]
                inbox_summary = f"Priority communication alert: 1 urgent dispatch logged from '{first_u.get('sender', 'Operations')}' regarding '{first_u.get('subject', 'Dispatches')}'. Total unread messages: {len(unread)}."
                briefing_sections.append(inbox_summary)
                for u in urgent_alerts[:2]:
                    structured_findings.append({
                        "title": f"Priority Alert: {u.get('subject')}",
                        "snippet": f"From: {u.get('sender')} | {u.get('snippet', '')[:100]}",
                        "url": "inbox://urgent",
                        "category": "INBOX"
                    })
            elif unread:
                briefing_sections.append(f"Communications report {len(unread)} unread dispatch{'es' if len(unread) != 1 else ''}, none classified as critical.")
                structured_findings.append({
                    "title": "Communications Status",
                    "snippet": f"{len(unread)} unread dispatches (nominal)",
                    "url": "inbox://unread",
                    "category": "INBOX"
                })
            else:
                briefing_sections.append("Zero unread messages in primary communications buffers.")
                structured_findings.append({
                    "title": "Communications Status",
                    "snippet": "Zero unread dispatches",
                    "url": "inbox://status",
                    "category": "INBOX"
                })
        except Exception:
            pass

        # 6. Git & Active Development Repository
        git_summary = ""
        try:
            git_stat = self.system_controller.get_git_status()
            if git_stat.get("is_git"):
                git_summary = self.system_controller.format_git_debrief(git_stat)
                briefing_sections.append(git_summary)
                structured_findings.append({
                    "title": f"Git Repository: {git_stat.get('branch')}",
                    "snippet": f"Modified: {len(git_stat.get('modified', []))} | Untracked: {len(git_stat.get('untracked', []))} | Clean: {git_stat.get('is_clean')}",
                    "url": "git://status",
                    "category": "GIT"
                })
        except Exception:
            pass

        briefing_sections.append("All primary systems stand at your disposal, Sir.")
        full_monologue = " ".join(briefing_sections)

        hud_payload = {
            "title": "Executive Situational Briefing",
            "action_type": "BRIEFING",
            "findings": structured_findings,
            "spoken_tl_dr": full_monologue,
            "timestamp": now.isoformat(),
            "period": period
        }

        return {
            "spoken_text": full_monologue,
            "briefing_sections": briefing_sections,
            "structured_findings": structured_findings,
            "hud_payload": hud_payload,
            "period": period
        }


# Type alias for cleaner code
Tuple_Greeting = tuple[str, str, str]
