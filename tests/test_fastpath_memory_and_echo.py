import time
from unittest.mock import MagicMock, patch
import pytest
from frontend.desktop import JarvisAPI
from core.jarvis_memory import JarvisMemory


# ─────────────────────────────────────────────────────────────────────────────
# Self-Echo Suppression Tests (Bug B)
# ─────────────────────────────────────────────────────────────────────────────

def test_self_echo_suppresses_genuine_partial_echo():
    """Verify genuine partial acoustic echo (microphone capturing J.A.R.V.I.S.'s speech) is suppressed."""
    api = JarvisAPI()
    api._tts_speaking = True
    api._recent_agent_responses = ["All diagnostic telemetry checks are clear, sir."]

    # Partial echo of the phrase (4 words, subset of past_clean)
    mic_text = "checks are clear sir"
    assert api._check_is_self_echo(mic_text) is True


def test_self_echo_suppresses_genuine_full_echo():
    """Verify exact acoustic echo during active playback is suppressed."""
    api = JarvisAPI()
    api._tts_playback_until = time.time() + 2.0
    api._recent_agent_responses = ["All diagnostic telemetry checks are clear, sir."]

    mic_text = "All diagnostic telemetry checks are clear, sir."
    assert api._check_is_self_echo(mic_text) is True


def test_self_echo_allows_user_referencing_prior_jarvis_text():
    """Verify that a user quoting or referring to previous J.A.R.V.I.S. text is NOT falsely suppressed."""
    api = JarvisAPI()
    api._tts_speaking = False
    api._tts_playback_until = time.time() + 1.0  # Even inside the reverb decay window!
    api._recent_agent_responses = [
        "Current CPU load is at 17.7%.",
        "All telemetry streams nominal.",
    ]

    # The exact query that triggered Bug B: user quoting past CPU load
    user_query = "J.A.R.V.I.S., you told about the CPU load, why it is 17.7%?"
    assert api._check_is_self_echo(user_query) is False


def test_self_echo_allows_speech_outside_reverb_window():
    """Verify that once audio finishes and the reverb window decays, no speech is suppressed as echo."""
    api = JarvisAPI()
    api._tts_speaking = False
    api._tts_playback_until = time.time() - 30.0  # Finished 30 seconds ago (outside 25s window)
    api._recent_agent_responses = ["All diagnostic telemetry checks are clear, sir."]

    # Even identical text is permitted once playback is completely over
    mic_text = "checks are clear sir"
    assert api._check_is_self_echo(mic_text) is False


def test_self_echo_allows_short_commands():
    """Verify that common short words/commands (<= 2 words) are never falsely tagged as echo."""
    api = JarvisAPI()
    api._tts_speaking = True
    api._recent_agent_responses = ["System status is fully operational, sir."]

    assert api._check_is_self_echo("status") is False
    assert api._check_is_self_echo("yes sir") is False
    assert api._check_is_self_echo("operational") is False


# ─────────────────────────────────────────────────────────────────────────────
# JarvisMemory Import & Fast-Path Tests (Bug A)
# ─────────────────────────────────────────────────────────────────────────────

def test_jarvis_memory_import_at_top_level():
    """Verify that JarvisMemory is available in frontend.desktop namespace."""
    import frontend.desktop as desktop_mod
    assert hasattr(desktop_mod, "JarvisMemory")
    assert desktop_mod.JarvisMemory is JarvisMemory


def test_fastpath_system_skill_diagnostics_no_name_error():
    """Verify system diagnostics skill fast path runs without NameError: name 'JarvisMemory' is not defined."""
    api = JarvisAPI()
    api._run_ask = MagicMock()
    api._emit = MagicMock()

    # Mock voice skills to simulate a system diagnostics hit (like CPU load)
    mock_voice = MagicMock()
    mock_skills = MagicMock()
    mock_skills.try_execute.return_value = (
        True,
        "CPU utilization is 17.7% across 16 logical cores.",
        False,
        "cpu load",
        {"action_type": "DIAGNOSTICS", "cpu_percent": 17.7}
    )
    mock_voice.skills = mock_skills
    mock_voice.persona_name = "jarvis"
    api._voice = mock_voice

    # Run the fast-path input
    api._run_process_input("why is cpu load 17.7%")

    # Ensure it called _run_ask with salutation filled and did not raise NameError
    api._run_ask.assert_called_once()
    prompt_arg = api._run_ask.call_args[0][0]
    assert "[SKILL_CONTEXT]" in prompt_arg
    assert "CPU utilization is 17.7%" in prompt_arg


def test_fastpath_set_operator_salutation_branch():
    """Verify set_operator_salutation fast-path branch runs without NameError."""
    api = JarvisAPI()
    api._emit = MagicMock()
    api._speak_and_suppress_echo = MagicMock()
    api._start_follow_up_window = MagicMock()

    mock_voice = MagicMock()
    mock_skills = MagicMock()
    mock_skills.try_execute.return_value = (
        True,
        "Call sign updated.",
        False,
        "set call sign",
        {"action_type": "set_operator_salutation", "salutation": "Commander"}
    )
    mock_voice.skills = mock_skills
    api._voice = mock_voice

    api._run_process_input("call me Commander")
    api._emit.assert_any_call("set_operator_salutation", {"salutation": "Commander"})


class ArcMatcher:
    def __eq__(self, other):
        return isinstance(other, dict) and other.get("action") == "arc"


def test_fastpath_route_plotting_salutation():
    """Verify _resolve_and_plot_route accesses JarvisMemory without error."""
    api = JarvisAPI()
    api._emit = MagicMock()
    api._speak_and_suppress_echo = MagicMock()
    api._start_follow_up_window = MagicMock()

    with patch.object(api, "resolve_coords") as mock_resolve:
        mock_resolve.side_effect = lambda loc: (
            (11.42, 76.86, "Kotagiri") if "kotagiri" in loc.lower()
            else (11.01, 76.96, "Coimbatore") if "coimbatore" in loc.lower()
            else None
        )
        handled = api._resolve_and_plot_route("route from Kotagiri to Coimbatore")
        assert handled is True
        api._emit.assert_any_call("annotate_map", ArcMatcher())


def test_jarvis_memory_api_methods():
    """Verify JarvisAPI public methods using JarvisMemory run cleanly."""
    api = JarvisAPI()
    api._emit = MagicMock()

    # 1. get_operator_salutation
    sal = api.get_operator_salutation()
    assert isinstance(sal, str)
    assert len(sal) > 0

    # 2. set_operator_salutation
    res = api.set_operator_salutation("Sir")
    assert res["status"] == "ok"
    assert res["salutation"] == "Sir"
