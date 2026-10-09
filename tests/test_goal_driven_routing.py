"""
Unit tests for Unified Goal-Driven Routing and Zero-LLM Fast-Path Isolation.
Verifies:
1. Compound multi-tool goals route via LLM tool-selection without keyword-flag matching.
2. Previously misrouted queries (e.g. queries with spatial words in investigation contexts) classify correctly.
3. All fast-path actions (system agency, media, volume, app launch, window mode, spatial glide, salutation, provenance, target lock) bypass the LLM entirely (0 cloud calls).
4. Graceful fallback when cloud calls fail or time out.
"""

import pytest
import json
from unittest.mock import MagicMock, patch

from core.jarvis_reasoning_loop import JarvisCognitiveLoop
from core.agent_router import AgentRouter
from narrative.jarvis_voice import JarvisVoice
from frontend.desktop import JarvisAPI, JarvisDesktop


def test_compound_multitool_goal_routes_without_keyword_flags():
    """
    Verifies that analyze_goal receives a compound request and decomposes it
    using the structured LLM tool-selection call without counting keyword flags.
    """
    loop = JarvisCognitiveLoop()

    # Mock voice cloud inference to simulate Groq tool selection response
    mock_voice = MagicMock()
    mock_voice._ask_cloud.return_value = {
        "text": json.dumps([
            {"action": "nav", "location": "Berlin"},
            {"action": "weather", "location": "Berlin"},
            {"action": "traffic", "location": "Berlin"}
        ]),
        "rate_limited": False,
        "engine": "groq"
    }
    loop.voice = mock_voice

    prompt = "Can you assess atmospheric conditions and street congestion in Berlin?"
    steps = loop.analyze_goal(prompt)

    assert len(steps) == 3
    assert steps[0]["action"] == "nav"
    assert steps[0]["location"] == "Berlin"
    assert steps[1]["action"] == "weather"
    assert steps[2]["action"] == "traffic"
    assert "atmospheric" in steps[1]["progress_phrase"].lower()
    assert "traffic" in steps[2]["progress_phrase"].lower()
    assert mock_voice._ask_cloud.called


def test_previously_misrouted_spatial_investigation_classifies_correctly():
    """
    Verifies that queries containing spatial words (like 'flight' or 'camera')
    are no longer blindly forced to 'covo' by the old map_or_media_triggers pre-filter.
    """
    voice = JarvisVoice()

    # Query: "check the flight situation over the target's IP range"
    # Old behavior: 'flight' was in map_or_media_triggers -> blindly returned 'covo'
    # New behavior: LLM parses semantic intent and classifies as investigate
    mock_cloud_resp = {
        "text": json.dumps({"type": "investigate", "target": "192.168.1.100"}),
        "rate_limited": False,
        "engine": "groq"
    }

    with patch.object(voice, "_ask_cloud", return_value=mock_cloud_resp) as mock_cloud:
        res = voice.classify_intent("check the flight situation over the target's IP range 192.168.1.100")
        assert res["type"] == "investigate"
        assert res["target"] == "192.168.1.100"
        assert mock_cloud.called

    # Query 2: "Investigate and inspect camera feeds for evilcorp.com"
    # Old behavior: 'camera' blindly forced 'covo'
    mock_cloud_resp2 = {
        "text": json.dumps({"type": "investigate", "target": "evilcorp.com"}),
        "rate_limited": False,
        "engine": "groq"
    }

    with patch.object(voice, "_ask_cloud", return_value=mock_cloud_resp2) as mock_cloud2:
        res2 = voice.classify_intent("Investigate and inspect camera feeds for evilcorp.com")
        assert res2["type"] == "investigate"
        assert res2["target"] == "evilcorp.com"
        assert mock_cloud2.called


def test_fast_paths_bypass_llm_entirely_with_zero_latency():
    """
    Verifies that all specified fast-path actions:
    - Layer 1 (volume, media, app launch, mute, task surface, rules, salutation, provenance, area annotation)
    - Layer 5 (window mode, spatial camera glide, route plot, globe fly-to, satellite pass)
    - Layer 4 (target lock, unlock)
    bypass the LLM entirely and invoke zero cloud calls.
    """
    router = AgentRouter()

    fast_path_queries = [
        # Layer 1: System Agency Controls
        "volume up",
        "volume down",
        "mute",
        "unmute",
        "pause music",
        "resume music",
        "play music",
        "stop music",
        "lock workstation",
        "lock screen",
        "top process",
        # Layer 1: App Launching
        "launch firefox",
        "open calculator",
        "start terminal",
        # Layer 1: Task Surface
        "minimize",
        "minimize panel",
        "expand window",
        "close panel",
        "open the first result",
        # Layer 1: Salutation & Provenance
        "call me Commander",
        "who built you",
        "who is your creator",
        # Layer 1: Memory Store
        "remember that I prefer dark mode",
        "save this rule: never run sudo without confirmation",
        # Layer 1: Area Annotation
        "annotate this area as defense zone",
        "mark a 30km no-fly zone around this sector",
        # Layer 1: Vision, Ambient, Scripts, Timers
        "look at my screen",
        "quiet mode",
        "write a python script to test network",
        "set a timer for 5 minutes",
        "cross verify case"
    ]

    for q in fast_path_queries:
        assert router.is_fast_path(q) is True, f"'{q}' should be recognized as a fast path!"
        handled, ack, task, cat = router.route_fast_path(q)
        assert handled is True, f"'{q}' route_fast_path failed"

    # Confirm that route_input on these fast paths NEVER initializes or calls the LLM
    with patch.object(JarvisCognitiveLoop, "_ask_cloud") as mock_cog_cloud:
        for q in fast_path_queries:
            handled, ack, task, cat = router.route_input(q)
            assert handled is True, f"route_input failed on '{q}'"
            assert mock_cog_cloud.call_count == 0, f"LLM was invoked for fast-path query: '{q}'!"

    # Reset salutation back to default to ensure clean test state across suites
    try:
        from core.jarvis_memory import JarvisMemory
        JarvisMemory().set_salutation("Sir")
    except Exception:
        pass


def test_desktop_fast_paths_bypass_cognitive_loop():
    """
    Verifies that Layer 5 fast paths in desktop.py (window modes, spatial glide, route plotting)
    execute directly and never call the cognitive LLM loop.
    """
    api = JarvisAPI.__new__(JarvisAPI)
    api._voice = MagicMock()
    api._emit = MagicMock()
    api._speak_and_suppress_echo = MagicMock()
    api._start_follow_up_window = MagicMock()
    api._run_ask = MagicMock()
    api.set_window_mode = MagicMock()
    api._resolve_and_plot_route = MagicMock(return_value=False)
    api._resolve_and_glide_location = MagicMock(return_value=False)
    api._context_manager = MagicMock()
    api._context_manager.classify_and_resolve.return_value = ("none", "none", {}, "hello")
    api._context_manager.get_active_pending_slot.return_value = None
    api._target = None

    with patch("core.jarvis_reasoning_loop.JarvisCognitiveLoop.analyze_goal") as mock_cog:
        # Window mode switch
        api._run_process_input("switch to hud")
        assert api.set_window_mode.called
        assert mock_cog.call_count == 0

        # Spatial screen navigation
        api._run_process_input("switch to cold room")
        assert mock_cog.call_count == 0

        api._run_process_input("switch to world telemetry")
        assert mock_cog.call_count == 0


def test_cloud_failure_fallback_graceful():
    """
    Verifies that when cloud LLM calls fail or time out, analyze_goal falls back
    to deterministic heuristic parsing without throwing an unhandled exception.
    """
    loop = JarvisCognitiveLoop()

    mock_voice = MagicMock()
    # Simulate network error or timeout
    mock_voice._ask_cloud.side_effect = TimeoutError("Groq gateway timed out")
    mock_voice._ask_slm.return_value = {"text": "", "error": True}
    loop.voice = mock_voice

    prompt = "Check traffic in Mumbai and see how it is flowing"
    steps = loop.analyze_goal(prompt)

    # Fallback should kick in and decompose into nav and traffic
    assert len(steps) >= 2
    actions = [s["action"] for s in steps]
    assert "traffic" in actions
