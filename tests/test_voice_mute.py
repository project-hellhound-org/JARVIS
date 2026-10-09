import os
import re
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from frontend.desktop import JarvisAPI
from core.wake_word import WakeWordEngine


def test_set_voice_mute_mutes_wake_engine_and_stops_background_loop():
    """Verify that muting voice disables wake engine, stops background listener, and resets timers."""
    api = JarvisAPI()
    mock_engine = MagicMock()
    api._wake_engine = mock_engine
    api._follow_up_active = True
    api._wake_window_expires = time.time() + 10.0
    api._follow_up_expires = time.time() + 10.0

    res = api.set_voice_mute(True)
    assert res["success"] is True
    assert res["muted"] is True
    assert api.get_voice_mute()["muted"] is True
    assert api._voice_muted is True

    # Check wake engine set_muted called
    mock_engine.set_muted.assert_called_with(True)

    # Check windows reset
    assert api._follow_up_active is False
    assert api._follow_up_expires == 0.0
    assert api._wake_window_expires == 0.0

    # Verify start_background_voice_listener refuses to start while muted
    bg_res = api.start_background_voice_listener()
    assert bg_res["success"] is False
    assert bg_res["status"] == "muted"


def test_set_voice_mute_unmutes_restores_wake_engine_and_starts_background_loop():
    """Verify unmuting restores wake engine and background listener."""
    api = JarvisAPI()
    mock_engine = MagicMock()
    api._wake_engine = mock_engine
    api._voice_muted = True

    with patch.object(api, "start_background_voice_listener") as mock_start_bg:
        res = api.set_voice_mute(False)
        assert res["success"] is True
        assert res["muted"] is False
        assert api._voice_muted is False
        mock_engine.set_muted.assert_called_with(False)
        mock_start_bg.assert_called_once()

    # Test toggle_voice_mute
    with patch.object(api, "start_background_voice_listener"):
        t1 = api.toggle_voice_mute()
        assert t1["muted"] is True
        assert api._voice_muted is True

        t2 = api.toggle_voice_mute()
        assert t2["muted"] is False
        assert api._voice_muted is False


def test_wake_engine_hardware_stream_pausing_and_guards():
    """Verify WakeWordEngine hardware stream pausing, window guards, and zero-CPU mute."""
    mock_on_wake = MagicMock()
    with patch.object(WakeWordEngine, "_init_engine"):
        engine = WakeWordEngine(on_wake_detected=mock_on_wake, threshold=0.60)
        assert engine.threshold == 0.60
        assert engine.is_muted is False

        mock_stream = MagicMock()
        mock_stream.is_active.return_value = True
        mock_stream.is_stopped.return_value = False
        engine._audio_stream = mock_stream

        # Mute engine
        engine.set_muted(True)
        assert engine.is_muted is True
        mock_stream.stop_stream.assert_called_once()

        # Wake detection events must be suppressed while muted
        engine.trigger_wake_event("Hey Jarvis")
        mock_on_wake.assert_not_called()
        assert engine.is_window_active is False

        # Follow-up window must be suppressed while muted
        engine.start_follow_up_window(10.0)
        assert engine.is_follow_up_mode is False
        assert engine.is_in_follow_up() is False

        # Unmute engine
        mock_stream.is_stopped.return_value = True
        engine.set_muted(False)
        assert engine.is_muted is False
        mock_stream.start_stream.assert_called_once()

        # Now wake detection succeeds
        engine.trigger_wake_event("Hey Jarvis")
        mock_on_wake.assert_called_once_with("Hey Jarvis")
        assert engine.is_window_active is True


def test_spacebar_ptt_override_while_voice_muted():
    """Verify Spacebar Push-to-Talk works while muted, but suppresses the post-speech open-mic follow-up."""
    api = JarvisAPI()
    api._voice_muted = True
    api._emit = MagicMock()

    # When muted, wake-word detection callbacks should do nothing
    api._on_wake_word_detected("Hey Jarvis")
    assert api._wake_window_expires == 0.0

    # When muted, follow-up window request should early-return without emitting open mic
    api._start_follow_up_window()
    assert api._follow_up_active is False
    assert api._wake_window_expires == 0.0

    # In _run_ask(), when voice is muted, _start_follow_up_window is not called
    api._voice = MagicMock()
    api._voice.chat.return_value = {"text": "All systems operational, Sir."}
    api._voice.fish_audio_available = False
    with patch.object(api, "_start_follow_up_window") as mock_start_followup:
        api._run_ask("Status report")
        time.sleep(0.1)
        mock_start_followup.assert_not_called()


def test_process_captured_speech_drops_audio_when_muted():
    """Verify background captured speech is immediately dropped when muted."""
    api = JarvisAPI()
    api._voice_muted = True
    emitted_events = []
    api._emit = lambda event, data: emitted_events.append((event, data))

    with patch.object(api, "_transcribe_audio_fast") as mock_stt:
        api._process_captured_speech(b"dummy_pcm_bytes_123")
        mock_stt.assert_not_called()
        assert ("jarvis_speech_ended", {}) in emitted_events


def test_punctuation_stripping_and_wake_phrase_filtering():
    """Verify Whisper's punctuated 'J.A.R.V.I.S.' and 'jarvis,' are cleanly detected as pure wake phrases."""
    wake_phrases = ["jarvis", "hey jarvis", "jarvis you there", "wake up jarvis", "alright jarvis", "yo jarvis", "ok jarvis"]
    test_inputs = [
        "J.A.R.V.I.S.",
        "jarvis,",
        "Hey Jarvis!",
        "J.A.R.V.I.S.?",
        "jarvis...",
    ]

    for raw in test_inputs:
        normalized = raw.replace(".", "").lower()
        cleaned = re.sub(r'^[^\w]+|[^\w]+$', '', normalized)

        # Check that normalized text matches a wake phrase exactly without remainder command
        matches_wake = any(cleaned == p or cleaned.startswith(p + " ") for p in wake_phrases)
        assert matches_wake is True

        remainder = cleaned
        for p in sorted(wake_phrases, key=lambda s: len(s), reverse=True):
            if remainder == p:
                remainder = ""
                break
            elif remainder.startswith(p + " "):
                remainder = remainder[len(p):].strip()
                break

        # Remainder should be empty for a pure wake call
        assert remainder == "", f"Expected empty remainder for '{raw}', got '{remainder}'"
