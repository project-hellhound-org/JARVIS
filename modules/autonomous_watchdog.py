"""
J.A.R.V.I.S. Autonomous Ambient Watchdog Engine.

Monitors real-world orbital and local system telemetry during operator idle intervals:
1. User Idle Time Tracking:
   - Tracks keyboard/mic activity and triggers ambient scans when idle exceeds `idle_alert_interval_min`.
2. Power-off & Laptop Sleep Resilience:
   - Persists state and heartbeats to `data/watchdog_state.json`.
   - Automatically provides a catch-up situational briefing upon boot if laptop was powered off.
3. Ambient Telemetry Scanners:
   - System Vitals: High CPU, depleted RAM, full disk, critical battery reserve.
   - Emergency Transponders: ADS-B squawk 7700 (General Emergency) and 7600 (Radio Loss).
   - Seismic Sensors: USGS real-time significant earthquakes (M5.5+).
4. Secure Multi-Channel Dispatch:
   - Delivers HUD updates, J.A.R.V.I.S. TTS voice alerts, Linux desktop notifications,
     and rate-limited external SMS (strictly sanitized with zero credential/PII leakage).
"""

import os
import time
import json
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from modules.secure_notifier import SecureNotifier, sanitize_text

try:
    import psutil
except ImportError:
    psutil = None

STATE_FILE = Path(__file__).parent.parent / "data" / "watchdog_state.json"


class AutonomousWatchdog:
    """
    Background autonomous observer for J.A.R.V.I.S.
    """

    _instance = None

    @classmethod
    def get_instance(cls, config: Optional[Dict[str, Any]] = None, **kwargs):
        if cls._instance is None:
            cls._instance = AutonomousWatchdog(config=config, **kwargs)
        return cls._instance

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        on_alert_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        voice_speak_callback: Optional[Callable[[str], None]] = None,
    ):
        self.config = config or self._load_config()
        self.on_alert_callback = on_alert_callback
        self.voice_speak_callback = voice_speak_callback
        self.notifier = SecureNotifier.get_instance(self.config)

        # Configurable intervals
        interval_min = float(self.config.get("idle_alert_interval_min", 45))
        self.idle_threshold_sec = max(60.0, interval_min * 60.0)
        self.enabled = bool(self.config.get("ambient_updates_enabled", True))

        self.last_user_activity = time.time()
        self.last_scan_time = 0.0
        self.last_alert_time = 0.0
        self._min_alert_cooldown_sec = 600.0  # At least 10 minutes between proactive interruptions

        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # State persistence
        self.seen_event_keys = set()
        self.boot_time = time.time()
        self.previous_shutdown_time = 0.0
        self.needs_catchup_briefing = False
        self.returned_after_sec = float(self.config.get("catchup_after_idle_hours", 4)) * 3600.0

        self._load_state()

    def _load_config(self) -> Dict[str, Any]:
        cfg_path = Path(__file__).parent.parent / "config.yaml"
        if cfg_path.exists():
            try:
                import yaml
                with open(cfg_path, "r") as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
        return {}

    def _load_state(self):
        """Restore watchdog state across reboots and detect power-off intervals."""
        try:
            if STATE_FILE.exists():
                with open(STATE_FILE, "r") as f:
                    data = json.load(f)
                    self.seen_event_keys = set(data.get("seen_event_keys", []))
                    self.last_alert_time = float(data.get("last_alert_time", 0.0))
                    last_hb = float(data.get("last_heartbeat", 0.0))
                    self.previous_shutdown_time = last_hb

                    # Detect if system was powered down or sleeping for > 1 hour
                    now = time.time()
                    if last_hb > 0 and (now - last_hb) > 3600.0:
                        self.needs_catchup_briefing = True
        except Exception as e:
            print(f"[autonomous_watchdog] State load notice: {e}")

    def _save_state(self):
        """Persist state to prevent alert re-firing across app restarts."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "last_heartbeat": time.time(),
                "last_alert_time": self.last_alert_time,
                "seen_event_keys": list(self.seen_event_keys)[-300:],  # Retain last 300
                "boot_time": self.boot_time,
            }
            with open(STATE_FILE, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            print(f"[autonomous_watchdog] State save notice: {e}")

    def record_user_activity(self):
        """Invoke whenever user types, speaks, or interacts with the console."""
        now = time.time()
        away = now - self.last_user_activity
        self.last_user_activity = now
        # Back after a long absence (without a reboot): the agents give a catch-up.
        if away >= self.returned_after_sec and not self.needs_catchup_briefing:
            threading.Thread(target=self._dispatch_catchup_briefing, args=(away / 3600.0,),
                             name="WatchdogCatchup", daemon=True).start()

    def get_idle_seconds(self) -> float:
        return time.time() - self.last_user_activity

    def is_idle(self) -> bool:
        return self.get_idle_seconds() >= self.idle_threshold_sec

    def start(self):
        """Start the watchdog background thread."""
        if not self.enabled:
            print("[autonomous_watchdog] Ambient updates disabled by configuration.")
            return

        if self.running:
            return

        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._watchdog_loop, name="AutonomousWatchdogThread", daemon=True)
        self._thread.start()
        print(f"[autonomous_watchdog] Ambient Idle Watchdog active (Interval: {self.idle_threshold_sec/60:.0f}m).")

    def stop(self):
        """Gracefully terminate background monitoring."""
        self.running = False
        self._stop_event.set()
        self._save_state()

    def _watchdog_loop(self):
        """Core telemetry monitoring loop."""
        # Initial settle delay
        time.sleep(10.0)

        # If laptop was powered off for hours, dispatch catchup briefing after network warms up
        if self.needs_catchup_briefing:
            self._dispatch_catchup_briefing()
            self.needs_catchup_briefing = False

        while self.running and not self._stop_event.is_set():
            try:
                self._save_state()

                # Check if idle threshold reached
                if self.is_idle():
                    now = time.time()
                    if (now - self.last_alert_time) >= self._min_alert_cooldown_sec:
                        self._perform_ambient_scan()
            except Exception as e:
                print(f"[autonomous_watchdog] Inspection cycle notice: {e}")

            # Sleep between loop iterations (20s)
            self._stop_event.wait(20.0)

    def _dispatch_catchup_briefing(self, hours_away: Optional[float] = None):
        """Brief operator on status after laptop power-off, sleep, or a long absence."""
        if hours_away is None:
            hours_away = max(1.0, (time.time() - self.previous_shutdown_time) / 3600.0) if self.previous_shutdown_time else 1.0
        # With the agent framework running, the briefing is built from live tools
        # (inbox, calendar, follow-ups) instead of a canned line.
        try:
            from core.agents.sensing import agents_listening, catchup_event, emit_event
            if agents_listening():
                briefing = None
                try:
                    from modules.situational_briefing import SituationalBriefingEngine
                    briefing = SituationalBriefingEngine().generate_briefing()
                except Exception:
                    briefing = None
                ev = catchup_event(hours_away, briefing)
                if emit_event(ev["source"], ev["kind"], ev["summary"], goal=ev["goal"], data=ev["data"],
                              severity=ev["severity"]):
                    self.notifier.send_desktop_notification(title="J.A.R.V.I.S. Online", message=ev["summary"],
                                                            urgency="normal")
                    return
        except Exception as e:
            print(f"[autonomous_watchdog] Agent catch-up notice: {e}")
        try:
            briefing_text = (
                "Welcome back, Sir. Orbital telemetry and local monitoring have resumed. "
                "All systems are operating within nominal parameters."
            )
            # Deliver desktop notification
            self.notifier.send_desktop_notification(
                title="J.A.R.V.I.S. Online",
                message=briefing_text,
                urgency="normal"
            )
            # Deliver to UI callback
            if self.on_alert_callback:
                self.on_alert_callback({
                    "title": "Situational Briefing",
                    "text": briefing_text,
                    "category": "system",
                    "urgency": "normal"
                })
            # Deliver to voice if available
            if self.voice_speak_callback:
                self.voice_speak_callback(briefing_text)
        except Exception as e:
            print(f"[autonomous_watchdog] Catchup briefing notice: {e}")

    def _perform_ambient_scan(self):
        """Scan real-world telemetry sources for anomalies while console is idle."""
        self.last_scan_time = time.time()

        # 1. System Health Telemetry
        sys_alert = self._scan_system_health()
        if sys_alert:
            self._dispatch_alert(sys_alert)
            return

        # 2. ADS-B Emergency Squawks (7700 / 7600)
        squawk_alert = self._scan_emergency_transponders()
        if squawk_alert:
            self._dispatch_alert(squawk_alert)
            return

        # 3. Seismic Telemetry (USGS M5.5+)
        seismic_alert = self._scan_seismic_telemetry()
        if seismic_alert:
            self._dispatch_alert(seismic_alert)
            return

    # ── Telemetry Scanners ──────────────────────────────────────────

    def _scan_system_health(self) -> Optional[Dict[str, Any]]:
        if not psutil:
            return None

        try:
            # Check CPU
            cpu = psutil.cpu_percent(interval=0.5)
            if cpu >= 92.0:
                key = f"sys_cpu_{int(time.time() / 1800)}"  # 30-min deduplication
                if key not in self.seen_event_keys:
                    self.seen_event_keys.add(key)
                    return {
                        "title": "System Telemetry Alert",
                        "text": f"Pardon the intrusion, Sir. System vitals indicate sustained CPU saturation at {cpu:.1f}%.",
                        "category": "hardware",
                        "urgency": "normal",
                        "external_sms": False
                    }

            # Check Memory
            mem = psutil.virtual_memory()
            if mem.percent >= 90.0:
                key = f"sys_mem_{int(time.time() / 1800)}"
                if key not in self.seen_event_keys:
                    self.seen_event_keys.add(key)
                    return {
                        "title": "Memory Depletion Alert",
                        "text": f"Sir, available system memory has dropped to {100 - mem.percent:.1f}%. Recommend inspecting memory-intensive processes.",
                        "category": "hardware",
                        "urgency": "normal",
                        "external_sms": False
                    }

            # Check Battery
            bat = psutil.sensors_battery()
            if bat and not bat.power_plugged and bat.percent <= 12:
                key = f"sys_bat_{bat.percent}"
                if key not in self.seen_event_keys:
                    self.seen_event_keys.add(key)
                    return {
                        "title": "Critical Battery Reserve",
                        "text": f"Sir, battery reserves have dropped to {bat.percent}%. Connection to auxiliary power is advised.",
                        "category": "hardware",
                        "urgency": "critical",
                        "external_sms": True
                    }
        except Exception:
            pass

        return None

    def _scan_emergency_transponders(self) -> Optional[Dict[str, Any]]:
        """Query ADS-B emergency transponder feeds."""
        url = "https://api.adsb.lol/v2/squawk/7700"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Tactical-OSINT/2.0"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                if resp.status == 200:
                    payload = json.loads(resp.read().decode("utf-8"))
                    aircraft = payload.get("ac", [])
                    for ac in aircraft:
                        hex_code = (ac.get("hex") or "").lower().strip()
                        callsign = (ac.get("flight") or ac.get("r") or hex_code).strip()
                        squawk = ac.get("squawk", "7700")
                        event_key = f"squawk_{hex_code}_{squawk}"
                        if hex_code and event_key not in self.seen_event_keys:
                            self.seen_event_keys.add(event_key)
                            alt = ac.get("alt_baro", "unknown altitude")
                            return {
                                "title": f"ADS-B Emergency Squawk {squawk}",
                                "text": f"Sir, transponder telemetry has flagged an active emergency squawk {squawk} on aircraft {callsign} at {alt} feet.",
                                "category": "aviation",
                                "urgency": "critical",
                                "external_sms": True
                            }
        except Exception:
            pass

        return None

    def _scan_seismic_telemetry(self) -> Optional[Dict[str, Any]]:
        """Query USGS significant earthquakes."""
        url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_hour.geojson"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Geoint/2.0"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    features = data.get("features", [])
                    for f in features:
                        eid = f.get("id")
                        props = f.get("properties", {})
                        mag = props.get("mag", 0.0)
                        place = props.get("place", "unspecified location")
                        if eid and eid not in self.seen_event_keys and mag >= 5.5:
                            self.seen_event_keys.add(eid)
                            return {
                                "title": f"Seismic Anomaly M{mag:.1f}",
                                "text": f"Sir, orbital seismic monitoring reports a magnitude {mag:.1f} earthquake near {place}.",
                                "category": "seismic",
                                "urgency": "normal",
                                "external_sms": False
                            }
        except Exception:
            pass

        return None

    # ── Dispatching ──────────────────────────────────────────────────

    def _dispatch_alert(self, alert: Dict[str, Any]):
        """Dispatches an alert to desktop notification, HUD, voice, and SMS."""
        self.last_alert_time = time.time()
        title = alert.get("title", "J.A.R.V.I.S. Telemetry")
        text = alert.get("text", "")
        urgency = alert.get("urgency", "normal")
        ext_sms = alert.get("external_sms", False)

        # 1. Native Desktop Notification (Linux notify-send)
        self.notifier.send_desktop_notification(title, text, urgency=urgency)

        # 2. External SMS / Webhook (if enabled / critical)
        if ext_sms or self.config.get("sms_alerts_enabled", False):
            self.notifier.send_sms_alert(f"{title}: {text}")

        # 3. Frontend Desktop HUD Callback
        if self.on_alert_callback:
            try:
                self.on_alert_callback(alert)
            except Exception as e:
                print(f"[autonomous_watchdog] HUD alert callback notice: {e}")

        # 4. Agent framework (looks into it, holds routine news during meetings, speaks) ...
        handed_over = False
        try:
            from core.agents.sensing import emit_event, watchdog_event
            ev = watchdog_event(alert)
            handed_over = emit_event(ev["source"], ev["kind"], ev["summary"], goal=ev["goal"], data=ev["data"],
                                     severity=ev["severity"])
        except Exception:
            handed_over = False

        # 5. ... or Voice TTS Speech when the agents aren't running
        if self.voice_speak_callback and text and not handed_over:
            try:
                self.voice_speak_callback(text)
            except Exception as e:
                print(f"[autonomous_watchdog] Voice alert callback notice: {e}")

        self._save_state()

    def get_status(self) -> Dict[str, Any]:
        """Diagnostic state for UI and API inspection."""
        return {
            "enabled": self.enabled,
            "running": self.running,
            "idle_threshold_sec": self.idle_threshold_sec,
            "idle_duration_sec": round(self.get_idle_seconds(), 1),
            "is_idle": self.is_idle(),
            "last_user_activity": self.last_user_activity,
            "last_scan_time": self.last_scan_time,
            "last_alert_time": self.last_alert_time,
            "seen_events_count": len(self.seen_event_keys)
        }
