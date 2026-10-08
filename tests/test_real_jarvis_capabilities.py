# tests/test_real_jarvis_capabilities.py
"""
Unit tests validating J.A.R.V.I.S.'s authentic real-system capabilities:
1. Real hardware diagnostics (SystemDiagnosticsEngine via psutil).
2. Real local document/file search (CloudDocumentManager replacing static PDF mocks).
3. Real OSRM driving navigation & geodesic calculation (MapsNavigationEngine).
4. Persona alignment and verbal debrief integrity (Stark voice, Sir addressing, zero slang).
5. Real Calendar .ics parsing and conflict detection (CalendarIntelManager).
6. Real Inbox alert scanning and communications intelligence (InboxIntelManager).
7. Smart Home protocols & arrival macros (SmartHomeManager).
8. Deadweight purge validation (absence of gods_eye clone).
"""

import os
import pytest
from pathlib import Path
from modules.system_diagnostics import SystemDiagnosticsEngine
from modules.cloud_docs import CloudDocumentManager
from modules.maps_nav import MapsNavigationEngine
from modules.calendar_intel import CalendarIntelManager
from modules.inbox_intel import InboxIntelManager
from modules.smart_home import SmartHomeManager
from core.system_skills import SystemSkillEngine
from narrative.jarvis_voice import JarvisVoice


class TestSystemDiagnostics:
    def setup_method(self):
        self.engine = SystemDiagnosticsEngine()

    def test_hardware_metrics_structure(self):
        metrics = self.engine.get_metrics()
        assert isinstance(metrics, dict)
        assert "cpu_percent" in metrics
        assert "ram_total_gb" in metrics
        assert "disk_total_gb" in metrics
        assert "thermals" in metrics
        assert "battery" in metrics
        assert "top_processes" in metrics
        assert "uptime_hours" in metrics

        # Verify real values from host
        assert metrics["cpu_percent"] >= 0.0
        assert metrics["ram_total_gb"] > 0.0
        assert metrics["ram_percent"] >= 0.0
        assert metrics["disk_total_gb"] > 0.0
        assert isinstance(metrics["top_processes"], list)

    def test_spoken_briefing_persona(self):
        metrics = self.engine.get_metrics()
        briefing = self.engine.format_tactical_debrief(metrics)
        assert isinstance(briefing, str)
        assert len(briefing) > 10
        # Must address operator as Sir
        assert "Sir" in briefing
        # Zero vulgarity or slang
        for taboo in ["dumbass", "dipshit", "buddy", "bruh", "partner"]:
            assert taboo not in briefing.lower()

    def test_hud_payload_generation(self):
        metrics = self.engine.get_metrics()
        payload = self.engine.build_hud_payload(metrics)
        assert isinstance(payload, dict)
        assert payload.get("action_type") == "DIAGNOSTIC"
        assert "findings" in payload
        assert "spoken_tl_dr" in payload


class TestRealDocumentSearch:
    def setup_method(self):
        self.doc_mgr = CloudDocumentManager()

    def test_real_workspace_file_indexing(self):
        res = self.doc_mgr.search_documents("README")
        assert isinstance(res, list)
        assert len(res) > 0

        first = res[0]
        assert "filename" in first
        assert "path" in first
        assert "size_kb" in first
        assert "summary" in first
        assert os.path.exists(first["path"])

    def test_code_file_search(self):
        res = self.doc_mgr.search_documents("jarvis")
        assert len(res) > 0
        filenames = [d["filename"] for d in res]
        assert any("jarvis.py" in f for f in filenames)

    def test_no_synthetic_pdf_mocks(self):
        res = self.doc_mgr.search_documents("Stark_Industries_Q3_Financials")
        assert len(res) == 0

    def test_read_document_summary_persona(self):
        summary = self.doc_mgr.get_document_summary("README")
        assert isinstance(summary, str)
        assert "Sir" in summary
        assert "README" in summary


class TestRealNavigationEngine:
    def setup_method(self):
        self.nav = MapsNavigationEngine()

    def test_distance_and_route_calculation(self):
        route = self.nav.get_route_directions("Bengaluru", origin="Chennai")
        assert route["distance_km"] > 250.0
        assert route["distance_km"] < 450.0
        assert "steps" in route
        assert len(route["steps"]) > 0
        assert "jarvis_prompts" in route

    def test_spoken_directions_persona(self):
        route = self.nav.get_route_directions("Bengaluru", origin="Chennai")
        prompts = " ".join(route["jarvis_prompts"])
        assert "Sir" in prompts
        for taboo in ["dumbass", "dipshit", "bruh", "buddy"]:
            assert taboo not in prompts.lower()

    def test_poi_search(self):
        pois = self.nav.search_nearby_poi("hospital", target_city="Chennai")
        assert isinstance(pois, list)
        assert len(pois) > 0
        assert "name" in pois[0]


class TestCalendarIntelProtocol:
    def setup_method(self):
        self.cal = CalendarIntelManager()

    def test_add_and_get_upcoming_events(self):
        from datetime import datetime, timedelta
        future_start = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d 10:00")
        future_end = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d 11:00")
        ack = self.cal.add_event("Project Hellhound Review", future_start, future_end, "HQ")
        assert "Sir" in ack
        upcoming = self.cal.get_upcoming_events(limit=10)
        assert any("Project Hellhound Review" in e.get("title", "") for e in upcoming)

    def test_conflict_detection(self):
        conflicts = self.cal.check_conflicts()
        assert isinstance(conflicts, list)

    def test_ics_parser_logic(self, tmp_path):
        sample_ics = tmp_path / "test_calendar.ics"
        sample_ics.write_text(
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "UID:uid12345\n"
            "SUMMARY:Orbital Defense Briefing\n"
            "DTSTART:20261015T140000Z\n"
            "DTEND:20261015T150000Z\n"
            "LOCATION:Sector 7 Command\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        parsed = self.cal._parse_ics(sample_ics)
        assert len(parsed) == 1
        assert parsed[0]["title"] == "Orbital Defense Briefing"
        assert parsed[0]["start"] == "2026-10-15 14:00"


class TestInboxIntelProtocol:
    def setup_method(self):
        self.inbox = InboxIntelManager()

    def test_urgent_alert_scanning(self):
        urgent = self.inbox.scan_urgent()
        assert isinstance(urgent, list)
        assert len(urgent) > 0
        # High-priority alerts must have urgent flag True
        for m in urgent:
            assert m.get("urgent") is True

    def test_receive_incoming_message(self):
        self.inbox.receive_incoming_message(
            sender="Cloud Ops <ops@aws.amazon.com>",
            subject="Critical Incident: Database Replica Offline",
            snippet="Production replica node reported unreachable at 12:00 UTC."
        )
        summary = self.inbox.get_tldr_summary()
        assert "Sir" in summary
        assert "Critical Incident" in summary or "Replica Offline" in summary

    def test_search_inbox(self):
        res = self.inbox.search_inbox("Security")
        assert isinstance(res, list)


class TestSmartHomeProtocols:
    def setup_method(self):
        self.home = SmartHomeManager()

    def test_device_controls(self):
        light_ack = self.home.set_light_state(True, brightness=80)
        assert "Sir" in light_ack
        assert "80%" in light_ack

        thermo_ack = self.home.set_thermostat(70)
        assert "Sir" in thermo_ack
        assert "70°F" in thermo_ack

        lock_ack = self.home.set_lock_state(True)
        assert "Sir" in lock_ack

    def test_arrival_protocol_execution(self):
        arrival_msg = self.home.handle_arrival()
        assert "Sir" in arrival_msg
        assert "Welcome home" in arrival_msg


class TestDeadweightPurgeParity:
    def test_gods_eye_directory_purged(self):
        repo_root = Path(__file__).parent.parent
        gods_eye_dir = repo_root / "gods_eye"
        assert not gods_eye_dir.exists(), "gods_eye directory must be completely purged from repository"

    def test_desktop_module_clean_import(self):
        from frontend.desktop import JarvisDesktop, JarvisAPI, HTML_PATH
        assert HTML_PATH.exists()
        assert hasattr(JarvisDesktop, "launch")
        assert hasattr(JarvisAPI, "investigate")


class TestPersonaAndVoiceIntegrity:
    def test_calendar_intel_stark_tone(self):
        cal = CalendarIntelManager()
        reminders = cal.format_jarvis_reminders()
        assert isinstance(reminders, str)
        for taboo in ["dumbass", "dipshit", "bruh", "buddy"]:
            assert taboo not in reminders.lower()

    def test_smart_home_welcome_stark_tone(self):
        home = SmartHomeManager()
        welcome = home.handle_arrival()
        assert "Sir" in welcome
        assert "bastard" not in welcome.lower()

    def test_system_skills_diagnostics_dispatch(self):
        skills = SystemSkillEngine()
        handled, speech, is_search, query, payload = skills.try_execute("system diagnostic")
        assert handled is True
        assert "Sir" in speech
        assert payload.get("action_type") == "DIAGNOSTIC"

    def test_voice_sanitizer_cleans_dean_to_sir(self):
        voice = JarvisVoice()
        cleaned = voice._clean_reasoning("Good morning Dean, all systems are operational.")
        assert "Dean" not in cleaned
        assert "Sir" in cleaned
