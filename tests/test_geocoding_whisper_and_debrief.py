import time
import pytest
from frontend.desktop import resolve_geospatial_coordinates, JarvisAPI
from narrative.jarvis_voice import JarvisVoice
from core.system_skills import SystemSkillEngine


def test_geospatial_coordinates_resolution():
    """Verify landmark geocoding correctly handles phonetic variants and specific landmarks."""
    # Phonetic Chepauk Stadium
    res1 = resolve_geospatial_coordinates("Chepak Stadium")
    assert res1 is not None
    assert abs(res1[0] - 13.0628) < 0.01
    assert abs(res1[1] - 80.2793) < 0.01
    assert "Chepauk" in res1[2]

    # Combined Chepauk Stadium, Chennai
    res2 = resolve_geospatial_coordinates("Chepauk Stadium, Chennai")
    assert res2 is not None
    assert abs(res2[0] - 13.0628) < 0.01
    assert abs(res2[1] - 80.2793) < 0.01

    # Generic city
    res3 = resolve_geospatial_coordinates("Chennai")
    assert res3 is not None
    assert abs(res3[0] - 13.0827) < 0.01
    assert abs(res3[1] - 80.2707) < 0.01


def test_voice_rate_limit_cooldown():
    """Verify rate limit cooldown properties self-heal after cooldown period."""
    voice = JarvisVoice()
    assert voice.groq_rate_limited is False
    assert voice.nvidia_rate_limited is False

    # Trigger rate limit
    voice.groq_rate_limited = True
    assert voice.groq_rate_limited is True
    assert voice._groq_rate_limited_until > time.time()

    # Simulate 15s elapsed
    voice._groq_rate_limited_until = time.time() - 1.0
    assert voice.groq_rate_limited is False


def test_system_skills_telemetry_phrases():
    """Verify conversational resource queries route to SystemDiagnosticsEngine."""
    engine = SystemSkillEngine()
    test_queries = [
        "where we are in the resources",
        "how is the system",
        "system resources",
        "how are our resources",
        "check resources",
        "ram usage",
    ]
    for q in test_queries:
        matched, debrief, _, _, payload = engine.try_execute(q)
        assert matched is True, f"Failed matching query: {q}"
        assert "CPU" in debrief or "Diagnostics" in debrief or "nominal" in debrief
        assert "findings" in payload or "summary" in payload or "cpu_percent" in str(payload)


def test_tactical_annotate_pin_resolution():
    """Verify pin annotation prioritizes landmark label over generic city target."""
    api = JarvisAPI(initial_mode="full")
    emitted_events = []
    api._emit = lambda event, data: emitted_events.append((event, data))

    # Operator commanded: pin Chennai label="Chepak Stadium"
    api._execute_tactical_annotate('pin Chennai label="Chepak Stadium"')
    assert len(emitted_events) == 1
    ev, data = emitted_events[0]
    assert ev == "annotate_map"
    assert data["action"] == "pin"
    # Must resolve to Chepauk Stadium (~13.0628, 80.2793), NOT central Chennai (13.0827, 80.2707)
    assert abs(data["lat"] - 13.0628) < 0.01
    assert abs(data["lon"] - 80.2793) < 0.01
    assert "Chepak Stadium" in data["label"] or "Chepauk" in data["label"]


def test_debrief_aggregation_queue():
    """Verify command debriefs are queued and aggregated rather than firing concurrent asks."""
    api = JarvisAPI(initial_mode="full")
    api._voice = JarvisVoice()
    asks = []
    api._run_ask = lambda prompt: asks.append(prompt)

    # Dispatch 2 command debriefs in rapid succession
    api._on_command_debrief("df -h", {"success": True, "stdout": "/dev/sda1 100G 20G", "exit_code": 0}, "t1")
    api._on_command_debrief("free -h", {"success": True, "stdout": "Mem: 16Gi 4Gi", "exit_code": 0}, "t2")

    # Verify both are in pending queue
    with api._debrief_lock:
        assert len(api._pending_debriefs) == 2

    # Run aggregated debrief directly
    api._run_aggregated_debrief()

    # Verify only 1 aggregated prompt was sent
    assert len(asks) == 1
    assert "Executed Commands Summary (2 items)" in asks[0]
    assert "df -h" in asks[0]
    assert "free -h" in asks[0]
