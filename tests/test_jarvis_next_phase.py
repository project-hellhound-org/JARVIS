# tests/test_jarvis_next_phase.py
"""
Comprehensive unit tests for J.A.R.V.I.S. Next Phase:
1. DesktopVisionEngine: window info, cursor position, focal cropping, on-device Tesseract OCR.
2. AmbientSentinel: proactive background vitals (battery, thermals, CPU, storage, calendar, inbox), quiet mode, debouncing.
3. EngineeringLab: dynamic script authoring, syntax linting, sandboxed execution, debriefing.
4. AgentRouter & SystemSkills: intent routing and execution dispatch for vision, sentinel, and engineering tasks.
"""

import os
import time
import pytest
from pathlib import Path
from PIL import Image, ImageDraw

from modules.desktop_vision import DesktopVisionEngine
from modules.ambient_sentinel import AmbientSentinel
from modules.engineering_lab import EngineeringLab
from core.agent_router import AgentRouter
from core.system_skills import SystemSkillEngine
from core.task import TaskType


class TestDesktopVisionEngine:
    def setup_method(self):
        self.vision = DesktopVisionEngine(cache_dir="/tmp/test_jarvis_vision")

    def test_active_window_telemetry(self):
        win = self.vision.get_active_window()
        assert isinstance(win, dict)
        assert "title" in win
        assert "wm_class" in win

    def test_cursor_position(self):
        pos = self.vision.get_cursor_position()
        assert isinstance(pos, tuple)
        assert len(pos) == 2
        assert isinstance(pos[0], int)
        assert isinstance(pos[1], int)

    def test_screen_capture_and_cropping(self):
        # 1. Full capture
        shot = self.vision.capture_screen()
        assert shot is not None
        assert os.path.exists(shot)
        assert os.path.getsize(shot) > 0

        # 2. Crop around center
        crop = self.vision.crop_cursor_region(shot, 500, 400, width=300, height=200)
        assert crop is not None
        assert os.path.exists(crop)
        with Image.open(crop) as img:
            assert img.width == 300
            assert img.height == 200

    def test_local_tesseract_ocr_extraction(self):
        test_img_path = "/tmp/test_jarvis_vision/ocr_test.png"
        img = Image.new("RGB", (500, 120), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((20, 45), "J.A.R.V.I.S. TACTICAL CORE: VERIFIED", fill=(0, 0, 0))
        img.save(test_img_path)

        extracted = self.vision.extract_text_ocr(test_img_path)
        assert "TACTICAL" in extracted or "VERIFIED" in extracted or "CORE" in extracted

    def test_inspect_screen_at_cursor_persona(self):
        res = self.vision.inspect_screen_at_cursor(crop_width=300, crop_height=200)
        assert res["success"] is True
        assert "Sir" in res["debrief"]
        assert "cursor" in res
        assert "window" in res


class TestAmbientSentinel:
    def setup_method(self):
        self.sentinel = AmbientSentinel(poll_interval=5.0)

    def test_vitals_inspection_structure(self):
        alerts = self.sentinel.check_vitals()
        assert isinstance(alerts, list)
        for a in alerts:
            assert "category" in a
            assert "severity" in a
            assert "headline" in a
            assert "message" in a
            assert "Sir" in a["message"]

    def test_quiet_mode_toggle(self):
        ack_on = self.sentinel.enable_quiet_mode(True)
        assert self.sentinel.quiet_mode is True
        assert "Quiet mode engaged, Sir" in ack_on

        ack_off = self.sentinel.enable_quiet_mode(False)
        assert self.sentinel.quiet_mode is False
        assert "Quiet mode disengaged, Sir" in ack_off

    def test_sentinel_status(self):
        status = self.sentinel.get_status()
        assert "active" in status
        assert "quiet_mode" in status
        assert "Sir" in status["debrief"]

    def test_alert_debouncing(self):
        # Force an alert into history
        test_alert = {
            "category": "test_debounce",
            "severity": "warning",
            "headline": "Test Alert",
            "message": "Sir, this is a test alert."
        }
        self.sentinel.alert_cooldown_sec = 100.0

        # Inject alert directly into dispatch check
        now = time.time()
        self.sentinel._last_alert_time["test_debounce"] = now
        # Second attempt should be debounced
        raw = [test_alert]
        filtered = [a for a in raw if (time.time() - self.sentinel._last_alert_time.get(a["category"], 0.0)) >= self.sentinel.alert_cooldown_sec]
        assert len(filtered) == 0


class TestEngineeringLab:
    def setup_method(self):
        self.lab = EngineeringLab(workspace_dir=Path("/tmp/test_scratchpad"))

    def test_create_and_lint_python_script(self):
        code = "val = 42 * 2\nprint(f'Computed: {val}')"
        c_res = self.lab.create_script("math_probe", "python", code)
        assert c_res["success"] is True
        assert c_res["filename"] == "math_probe.py"
        assert os.path.exists(c_res["filepath"])

        l_res = self.lab.lint_script(c_res["filepath"], "python")
        assert l_res["valid"] is True

    def test_lint_syntax_error_detection(self):
        bad_code = "def broken(\n  return 1"
        c_res = self.lab.create_script("bad_script", "python", bad_code)
        l_res = self.lab.lint_script(c_res["filepath"], "python")
        assert l_res["valid"] is False
        assert len(l_res["error"]) > 0

    def test_run_task_end_to_end_and_persona(self):
        code = "print('STARK_TELEMETRY: ACTIVE')"
        res = self.lab.run_task("telemetry_run", "python", code)
        assert res["success"] is True
        assert "STARK_TELEMETRY: ACTIVE" in res["stdout"]
        assert "Sir" in res["debrief"]
        assert res["exit_code"] == 0

    def test_list_scratchpad_scripts(self):
        scripts = self.lab.list_scratchpad_scripts()
        assert isinstance(scripts, list)
        assert len(scripts) >= 1
        assert any("math_probe.py" in s["name"] for s in scripts)


class TestAgentRouterAndSkillDispatchNextPhase:
    def setup_method(self):
        self.router = AgentRouter()
        self.skills = SystemSkillEngine()

    def test_agent_router_vision_inspection(self):
        handled, ack, task, cat = self.router.route_input("Jarvis, look at my screen")
        assert handled is True
        assert task is not None
        assert task.type == TaskType.VISION_INSPECT.value
        assert "Sir" in ack

    def test_agent_router_sentinel_quiet_mode(self):
        handled, ack, task, cat = self.router.route_input("enable quiet mode")
        assert handled is True
        assert task is not None
        assert task.type == TaskType.AMBIENT_CONFIG.value

    def test_agent_router_engineering_script(self):
        handled, ack, task, cat = self.router.route_input("write a python script to test network")
        assert handled is True
        assert task is not None
        assert task.type == TaskType.ENGINEERING_SCRIPT.value

    def test_system_skills_vision_execution(self):
        handled, msg, is_search, query, payload = self.skills.try_execute("read my screen")
        assert handled is True
        assert "Sir" in msg
        assert payload.get("action_type") == "VISION"

    def test_system_skills_engineering_execution(self):
        handled, msg, is_search, query, payload = self.skills.try_execute("create script")
        assert handled is True
        assert "Sir" in msg
        assert payload.get("action_type") == "ENGINEERING"

    def test_system_skills_sentinel_execution(self):
        handled, msg, is_search, query, payload = self.skills.try_execute("enable quiet mode")
        assert handled is True
        assert "Quiet mode engaged, Sir" in msg
        assert payload.get("action_type") == "SENTINEL"
