# tests/test_bargein_and_reminders.py
"""
Unit tests for:
1. Proactive Natural-Language Reminders & Countdown Timers (ReminderService & AgentRouter)
2. Full-Duplex Conversational Barge-In & Voice Interruption (JarvisVoice & Desktop)
"""

import time
import pytest
from unittest.mock import MagicMock, patch
from modules.reminder_service import ReminderService
from core.agent_router import AgentRouter
from core.task import TaskType
from narrative.jarvis_voice import JarvisVoice


class TestReminderService:
    def setup_method(self):
        self.service = ReminderService(storage_path="/tmp/test_reminders.json")
        self.service.clear_all()

    def teardown_method(self):
        self.service.stop()

    def test_parse_relative_timer(self):
        now = time.time()
        target, label, is_timer = self.service.parse_time_and_label("set a timer for 5 minutes")
        assert is_timer is True
        assert target is not None
        assert 295 <= (target - now) <= 305
        assert "timer" in label.lower() or "5m" in label.lower()

    def test_parse_relative_seconds_timer(self):
        now = time.time()
        target, label, is_timer = self.service.parse_time_and_label("timer 45 seconds for pasta")
        assert is_timer is True
        assert target is not None
        assert 43 <= (target - now) <= 47
        assert "pasta" in label.lower()

    def test_parse_reminder_with_label(self):
        now = time.time()
        target, label, is_timer = self.service.parse_time_and_label("remind me in 10 minutes to check the server")
        assert target is not None
        assert 595 <= (target - now) <= 605
        assert "check the server" in label.lower()

    def test_add_timer_and_get_active(self):
        timer = self.service.add_timer(60, label="Tea timer")
        assert timer["status"] == "active"
        assert timer["duration_sec"] == 60

        active = self.service.get_active()
        assert len(active) == 1
        assert active[0]["id"] == timer["id"]
        assert 55 <= active[0]["remaining_sec"] <= 60

    def test_cancel_timer(self):
        timer = self.service.add_timer(120, label="Work block")
        assert len(self.service.get_active()) == 1

        cancelled = self.service.cancel(timer["id"])
        assert cancelled is True
        assert len(self.service.get_active()) == 0

    def test_timer_trigger_callback(self):
        alert_received = []
        self.service.voice_notifier = lambda msg: alert_received.append(msg)

        # Set 1 second timer
        self.service.add_timer(1, label="Quick chime")
        for _ in range(25):
            if len(alert_received) > 0 or len(self.service.get_active()) == 0:
                break
            time.sleep(0.1)

        assert len(alert_received) >= 1 or len(self.service.get_active()) == 0


class TestReminderRouting:
    def setup_method(self):
        self.router = AgentRouter()

    def test_routes_set_timer(self):
        handled, msg, task, action_cat = self.router.route_input("set a timer for 10 minutes")
        assert handled is True
        assert task is not None
        assert task.type == TaskType.REMINDER_TIMER.value

    def test_routes_remind_me(self):
        handled, msg, task, action_cat = self.router.route_input("remind me in 15 minutes to call Tony")
        assert handled is True
        assert task is not None
        assert task.type == TaskType.REMINDER_TIMER.value

    def test_routes_list_reminders(self):
        handled, msg, task, action_cat = self.router.route_input("what are my active timers")
        assert handled is True
        assert task is not None
        assert task.type == TaskType.REMINDER_TIMER.value

    def test_routes_cancel_timer(self):
        handled, msg, task, action_cat = self.router.route_input("cancel timer")
        assert handled is True
        assert task is not None
        assert task.type == TaskType.REMINDER_TIMER.value


class TestFullDuplexBargeIn:
    def test_voice_interrupt_sets_event_and_kills_proc(self):
        voice = JarvisVoice()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        voice._current_player_proc = mock_proc

        interrupted = voice.interrupt()
        assert interrupted is True
        assert voice._interrupted.is_set()
        mock_proc.terminate.assert_called_once()

    def test_desktop_cancel_playback(self):
        from frontend.desktop import JarvisAPI
        api = JarvisAPI()
        api._tts_playback_until = time.time() + 10.0
        mock_proc = MagicMock()
        api._current_tts_proc = mock_proc

        res = api.cancel_playback()
        assert res["success"] is True
        assert api._tts_playback_until == 0.0
        assert api._current_tts_proc is None
        mock_proc.terminate.assert_called_once()
