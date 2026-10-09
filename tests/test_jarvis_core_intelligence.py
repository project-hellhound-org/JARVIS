# tests/test_jarvis_core_intelligence.py
"""
Comprehensive verification test suite for J.A.R.V.I.S. Core Intelligence & Agency:
1. SystemController: volume/audio, clipboard intelligence, process monitoring & safe termination, git status.
2. SituationalBriefingEngine: time-aware salutations, multi-source synthesis (weather, hardware, calendar, inbox, git).
3. JarvisCognitiveLoop: composite multi-step goal decomposition and spoken progression execution.
4. AgentRouter: intent routing for briefings, system agency controls, and git intelligence.
5. SystemSkillEngine: execution dispatch of new task types and HUD payload generation.
"""

import os
import pytest
from pathlib import Path
from modules.system_controller import SystemController
from modules.situational_briefing import SituationalBriefingEngine
from core.jarvis_reasoning_loop import JarvisCognitiveLoop
from core.agent_router import AgentRouter
from core.system_skills import SystemSkillEngine
from core.task import TaskType


class TestSystemControllerAgency:
    def setup_method(self):
        self.controller = SystemController()

    def test_volume_controls_and_bounds(self):
        res = self.controller.set_volume(85)
        assert res["success"] is True
        assert res["volume"] == 85
        assert "Sir" in res["debrief"]

        # Bounds test
        res_high = self.controller.set_volume(150)
        assert res_high["volume"] == 100

        res_low = self.controller.set_volume(-20)
        assert res_low["volume"] == 0

    def test_mute_toggle(self):
        res_mute = self.controller.mute(True)
        assert res_mute["success"] is True
        assert res_mute["muted"] is True
        assert "muted, Sir" in res_mute["debrief"]

        res_unmute = self.controller.mute(False)
        assert res_unmute["success"] is True
        assert res_unmute["muted"] is False
        assert "unmuted, Sir" in res_unmute["debrief"]

    def test_clipboard_read_write_and_summarize(self):
        sample = "https://github.com/project-hellhound/JARVIS"
        self.controller.set_clipboard_text(sample)
        read_back = self.controller.get_clipboard_text()
        assert read_back == sample or len(read_back) > 0

        summary = self.controller.summarize_clipboard()
        assert isinstance(summary, str)
        assert "Sir" in summary

    def test_top_processes_audit(self):
        procs_cpu = self.controller.get_top_processes(limit=3, by="cpu")
        assert len(procs_cpu) > 0
        assert "pid" in procs_cpu[0]
        assert "name" in procs_cpu[0]
        assert "cpu_percent" in procs_cpu[0]

        procs_mem = self.controller.get_top_processes(limit=3, by="memory")
        assert len(procs_mem) > 0
        assert "memory_percent" in procs_mem[0]

    def test_kill_process_safety_guards(self):
        # Refuses PID 0, 1, or self
        res_self = self.controller.kill_process(str(os.getpid()))
        assert res_self["success"] is False
        assert "denied, Sir" in res_self["debrief"]

        # Refuses protected system names
        res_init = self.controller.kill_process("systemd")
        assert res_init["success"] is False
        assert "refused, Sir" in res_init["debrief"]

    def test_git_repository_telemetry(self):
        status = self.controller.get_git_status()
        assert status["is_git"] is True
        assert "branch" in status
        assert isinstance(status["modified"], list)

        debrief = self.controller.format_git_debrief(status)
        assert isinstance(debrief, str)
        assert "Sir" in debrief


class TestSituationalBriefing:
    def setup_method(self):
        self.briefing_engine = SituationalBriefingEngine()

    def test_time_aware_greeting_structure(self):
        salutation, time_phrase, period = self.briefing_engine._determine_greeting()
        assert any(salutation.startswith(s) for s in ["Good morning", "Good afternoon", "Good evening"])
        assert "Sir" in salutation
        assert "currently" in time_phrase
        assert period in ["morning", "afternoon", "evening"]

    def test_full_briefing_generation(self):
        res = self.briefing_engine.generate_briefing()
        assert isinstance(res, dict)
        assert "spoken_text" in res
        assert "hud_payload" in res

        spoken = res["spoken_text"]
        # Must address operator as Sir
        assert "Sir" in spoken
        # Must include hardware or system note
        assert any(term in spoken for term in ["nominal", "systems", "CPU", "RAM"])

        payload = res["hud_payload"]
        assert payload["action_type"] == "BRIEFING"
        assert len(payload["findings"]) >= 3


class TestCognitiveLoopExpansion:
    def setup_method(self):
        self.loop = JarvisCognitiveLoop()

    def test_composite_goal_decomposition_system_and_processes(self):
        prompt = "Check my system resources and tell me the highest CPU process"
        steps = self.loop.analyze_goal(prompt)
        actions = [s["action"] for s in steps]
        assert "diagnostics" in actions
        assert "top_processes" in actions

    def test_composite_goal_decomposition_git_and_calendar(self):
        prompt = "Check git status and check my calendar agenda"
        steps = self.loop.analyze_goal(prompt)
        actions = [s["action"] for s in steps]
        assert "git_intel" in actions
        assert "calendar" in actions

    def test_situational_briefing_step_in_cognitive_loop(self):
        prompt = "Jarvis give me a situational briefing"
        steps = self.loop.analyze_goal(prompt)
        actions = [s["action"] for s in steps]
        assert "briefing" in actions

    def test_multi_step_cognitive_execution_with_spoken_progression(self):
        spoken = []

        def _spk(phrase):
            spoken.append(phrase)

        prompt = "Check my system resources and tell me the top processes"
        res = self.loop.execute_plan(prompt, on_progress_speak=_spk)
        assert res["handled"] is True
        assert len(spoken) >= 2
        assert "All operational tasks completed, Sir" in res["text"]


class TestAgentRouterAndSkillEngineIntegration:
    def setup_method(self):
        self.router = AgentRouter()
        self.skills = SystemSkillEngine()

    def test_agent_router_situational_briefing(self):
        handled, ack, task, cat = self.router.route_input("Good morning Jarvis")
        assert handled is True
        assert task is not None
        assert task.type == TaskType.SITUATIONAL_BRIEFING.value
        assert "Sir" in ack

    def test_agent_router_volume_control(self):
        handled, ack, task, cat = self.router.route_input("Jarvis, volume down")
        assert handled is True
        assert task is not None
        assert task.type == TaskType.SYSTEM_CONTROL.value
        assert task.data.get("action_type") == "volume"

    def test_agent_router_clipboard(self):
        handled, ack, task, cat = self.router.route_input("What's on my clipboard?")
        assert handled is True
        assert task is not None
        assert task.type == TaskType.SYSTEM_CONTROL.value
        assert task.data.get("action_type") == "clipboard"

    def test_agent_router_git_status(self):
        handled, ack, task, cat = self.router.route_input("Check git status")
        assert handled is True
        assert task is not None
        assert task.type == TaskType.GIT_INTEL.value
        assert "Sir" in ack

    def test_system_skills_end_to_end_briefing(self):
        handled, msg, is_search, query, payload = self.skills.try_execute("give me a situational briefing")
        assert handled is True
        assert is_search is False
        assert "Sir" in msg
        assert payload.get("action_type") == "BRIEFING"

    def test_system_skills_end_to_end_git_telemetry(self):
        handled, msg, is_search, query, payload = self.skills.try_execute("check git status")
        assert handled is True
        assert is_search is False
        assert "Sir" in msg
        assert payload.get("action_type") == "GIT"
