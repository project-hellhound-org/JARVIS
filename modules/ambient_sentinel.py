"""
J.A.R.V.I.S. Ambient Sentinel & Proactive Guardian Engine ("Stark Guardian").

Continuously monitors system vitals and operator context in the background:
1. Battery reserve guardian (alerts at <20% and <10% when discharging).
2. Thermal dissipation & runaway CPU monitor (alerts at >85°C with culprit process).
3. Filesystem storage depletion guard (alerts when free space drops below 5 GB).
4. Calendar radar (proactive 5-minute advance notice for upcoming meetings).
5. Urgent communications dispatcher (proactive alerts for critical inbox items).
6. Smart alert debouncing and Quiet Mode toggle.
"""

import os
import time
import shutil
import psutil
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable

from core.event_bus import get_event_bus, EventBus
from modules.calendar_intel import CalendarIntelManager
from modules.inbox_intel import InboxIntelManager


class AmbientSentinel:
    """
    Background autonomous telemetry guardian for J.A.R.V.I.S.
    """

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = AmbientSentinel()
        return cls._instance

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        poll_interval: float = 20.0,
        voice_notifier: Optional[Callable[[str], None]] = None
    ):
        self.bus = event_bus or get_event_bus()
        self.poll_interval = poll_interval
        self.voice_notifier = voice_notifier
        self.quiet_mode = False
        self.running = False
        self.thread: Optional[threading.Thread] = None

        # Thresholds
        self.battery_threshold_warn = 20
        self.battery_threshold_critical = 10
        self.thermal_threshold_c = 85.0
        self.cpu_threshold_pct = 92.0
        self.disk_threshold_gb = 5.0
        self.meeting_lookahead_min = 10

        # Alert debouncing / cooldown (seconds)
        self.alert_cooldown_sec = 900.0  # 15 minutes
        self._last_alert_time: Dict[str, float] = {}
        self._alert_history: List[Dict[str, Any]] = []

        self.calendar_mgr = CalendarIntelManager()
        self.inbox_mgr = InboxIntelManager()

    # ── 1. Vitals Inspection ──────────────────────────────────────────

    def check_vitals(self) -> List[Dict[str, Any]]:
        """
        Inspect all hardware and schedule vitals and return pending alerts.
        """
        alerts = []
        now_ts = time.time()

        # A. Battery check
        try:
            bat = psutil.sensors_battery()
            if bat and not bat.power_plugged:
                pct = int(bat.percent)
                if pct <= self.battery_threshold_critical:
                    alerts.append({
                        "category": "battery_critical",
                        "severity": "critical",
                        "headline": "Critical Battery Depletion",
                        "message": f"Sir, main battery reserves are critical at {pct}%. Please connect the AC power adapter immediately."
                    })
                elif pct <= self.battery_threshold_warn:
                    alerts.append({
                        "category": "battery_low",
                        "severity": "warning",
                        "headline": "Low Battery Warning",
                        "message": f"Sir, battery level is at {pct}% and discharging. You have approximately {int(bat.secsleft // 60)} minutes remaining."
                    })
        except Exception:
            pass

        # B. Thermals check
        try:
            temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
            peak_temp = 0.0
            peak_chip = "CPU"
            for chip, entries in temps.items():
                for e in entries:
                    cur = getattr(e, "current", 0.0)
                    if cur and cur > peak_temp and cur < 125.0:
                        peak_temp = cur
                        peak_chip = chip

            if peak_temp >= self.thermal_threshold_c:
                alerts.append({
                    "category": "thermal_overload",
                    "severity": "warning",
                    "headline": "Thermal Dissipation Warning",
                    "message": f"Thermal warning: {peak_chip} core temperature is peaking at {int(peak_temp)}°C, Sir. Recommending workload throttling."
                })
        except Exception:
            pass

        # C. Runaway CPU check
        try:
            cpu = psutil.cpu_percent(interval=None)
            if cpu >= self.cpu_threshold_pct:
                top_proc = "Unknown"
                for p in psutil.process_iter(['name', 'cpu_percent']):
                    try:
                        if p.info['cpu_percent'] and p.info['cpu_percent'] > 50.0:
                            top_proc = p.info['name']
                            break
                    except Exception:
                        continue
                alerts.append({
                    "category": "cpu_overload",
                    "severity": "warning",
                    "headline": "Sustained CPU Overload",
                    "message": f"Sir, processor load is sustained at {int(cpu)}%. Process '{top_proc}' is currently commanding peak execution resources."
                })
        except Exception:
            pass

        # D. Storage check
        try:
            usage = shutil.disk_usage("/")
            free_gb = round(usage.free / (1024 ** 3), 2)
            if free_gb < self.disk_threshold_gb:
                alerts.append({
                    "category": "storage_low",
                    "severity": "warning",
                    "headline": "Storage Space Depletion",
                    "message": f"Storage warning, Sir: Primary disk volume has only {free_gb} gigabytes remaining."
                })
        except Exception:
            pass

        # E. Upcoming Calendar check
        try:
            upcoming = self.calendar_mgr.get_upcoming_events(limit=3)
            now = datetime.now()
            for ev in upcoming:
                st_str = ev.get("start", "")
                if st_str:
                    try:
                        ev_dt = datetime.strptime(st_str, "%Y-%m-%d %H:%M")
                        diff_sec = (ev_dt - now).total_seconds()
                        if 0 <= diff_sec <= (self.meeting_lookahead_min * 60):
                            mins = max(1, int(diff_sec // 60))
                            title = ev.get("title", "Meeting")
                            alerts.append({
                                "category": f"calendar_{ev.get('id', title)}",
                                "severity": "info",
                                "headline": f"Approaching Engagement: {title}",
                                "message": f"Sir, a brief reminder: '{title}' is scheduled to begin in approximately {mins} minute{'s' if mins != 1 else ''}."
                            })
                    except Exception:
                        continue
        except Exception:
            pass

        # F. Urgent Communications check
        try:
            urgent = self.inbox_mgr.scan_urgent_alerts()
            if urgent:
                u = urgent[0]
                alerts.append({
                    "category": f"inbox_{u.get('id', 'urgent')}",
                    "severity": "warning",
                    "headline": f"Priority Dispatch: {u.get('subject', 'Alert')}",
                    "message": f"Sir, priority dispatch received from '{u.get('sender', 'Operations')}' regarding '{u.get('subject', 'Urgent')}'."
                })
        except Exception:
            pass

        return alerts

    # ── 2. Alert Dispatch & Debouncing ────────────────────────────────

    def process_and_dispatch(self) -> List[Dict[str, Any]]:
        """
        Check vitals, filter through debouncing and quiet mode, and dispatch notifications.
        """
        raw_alerts = self.check_vitals()
        dispatched = []
        now_ts = time.time()

        for alert in raw_alerts:
            cat = alert["category"]
            sev = alert.get("severity", "info")
            last = self._last_alert_time.get(cat, 0.0)

            # Check cooldown
            if (now_ts - last) < self.alert_cooldown_sec:
                continue

            # Check quiet mode (only critical alerts bypass quiet mode)
            if self.quiet_mode and sev != "critical":
                continue

            # Record dispatch
            self._last_alert_time[cat] = now_ts
            alert["timestamp"] = datetime.now().isoformat()
            self._alert_history.append(alert)
            if len(self._alert_history) > 50:
                self._alert_history.pop(0)

            dispatched.append(alert)

            # Emit via EventBus
            try:
                self.bus.emit("ambient_alert", alert)
            except Exception:
                pass

            # Hand it to the agent framework when it's running (it looks into the alert,
            # holds routine ones during meetings, and speaks); otherwise speak it here.
            try:
                from core.agents.sensing import emit_event, sentinel_event
                ev = sentinel_event(alert)
                if emit_event(ev["source"], ev["kind"], ev["summary"], goal=ev["goal"], data=ev["data"],
                              severity=ev["severity"], timely=ev.get("timely", False)):
                    continue
            except Exception:
                pass

            # Vocalize if voice notifier hooked
            if self.voice_notifier and callable(self.voice_notifier):
                try:
                    self.voice_notifier(alert["message"])
                except Exception:
                    pass

        return dispatched

    # ── 3. Quiet Mode & Controls ──────────────────────────────────────

    def enable_quiet_mode(self, enabled: bool = True) -> str:
        """
        Toggle quiet mode to silence non-critical background alerts.
        """
        self.quiet_mode = enabled
        if enabled:
            return "Quiet mode engaged, Sir. Ambient background voice alerts suppressed."
        else:
            return "Quiet mode disengaged, Sir. Proactive ambient telemetry restored."

    def get_status(self) -> Dict[str, Any]:
        """
        Query current status of the Ambient Sentinel.
        """
        return {
            "active": self.running,
            "quiet_mode": self.quiet_mode,
            "poll_interval_sec": self.poll_interval,
            "recent_alerts_count": len(self._alert_history),
            "recent_alerts": self._alert_history[-5:],
            "debrief": f"Ambient Sentinel is {'active' if self.running else 'standing by'} with Quiet Mode {'ON' if self.quiet_mode else 'OFF'}, Sir."
        }

    # ── 4. Lifecycle Thread Loop ──────────────────────────────────────

    def _loop(self):
        while self.running:
            try:
                self.process_and_dispatch()
            except Exception as e:
                print(f"[ambient_sentinel] Loop notice: {e}")
            time.sleep(self.poll_interval)

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="AmbientSentinel")
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
