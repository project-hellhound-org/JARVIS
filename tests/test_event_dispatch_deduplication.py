import pytest
from unittest.mock import MagicMock
from core.event_bus import get_event_bus
from core.jarvis_reasoning_loop import JarvisCognitiveLoop

def test_bridge_single_dispatch_for_cognitive_plan_execution():
    """
    Ensures that during execution of a cognitive plan, each action event
    is dispatched to the PyWebview bridge exactly once, and task events
    do not duplicate or wrap UI tool events.
    """
    bus = get_event_bus()
    dispatched_events = []

    def mock_bridge(event_name, payload):
        dispatched_events.append((event_name, payload))

    bus.set_bridge_callback(mock_bridge)

    loop = JarvisCognitiveLoop()
    # Plan execution involving navigation and flights layer
    plan_result = loop.execute_plan("track flights in London")
    assert plan_result.get("handled") is True

    # Collect event names received by the bridge
    event_names = [e[0] for e in dispatched_events]

    # Verify action events were emitted directly
    assert "glide_to_location" in event_names
    assert "toggle_tactical_layer" in event_names
    assert "jarvis_play_sfx" in event_names

    # Check count of glide_to_location is exactly 1 for the nav step
    assert event_names.count("glide_to_location") == 1
    # Check count of toggle_tactical_layer is exactly 1 for flights
    assert event_names.count("toggle_tactical_layer") == 1

    # Verify jarvis_task_event carries only task lifecycle events, never duplicate action events
    task_events = [e[1] for e in dispatched_events if e[0] == "jarvis_task_event"]
    for t_ev in task_events:
        sub_event = t_ev.get("event", "")
        assert sub_event.startswith("task."), f"Unexpected task event payload: {sub_event}"
        assert sub_event not in (
            "control_ground_intel",
            "control_tactical_layer",
            "toggle_tactical_layer",
            "glide_to_location",
            "control_cockpit",
            "jarvis_play_sfx"
        )

def test_six_bridge_events_single_dispatch():
    """
    Directly tests the six events named in Fix 2:
    control_cockpit, glide_to_location, glide_to_telemetry,
    jarvis_play_sfx, set_operator_salutation, toggle_tactical_layer.
    Verifies each reaches the frontend bridge exactly once.
    """
    bus = get_event_bus()
    dispatched = []

    def mock_bridge(event_name, payload):
        dispatched.append((event_name, payload))

    bus.set_bridge_callback(mock_bridge)

    test_events = [
        ("control_cockpit", {"action": "enter", "target": "AFR123"}),
        ("glide_to_location", {"lat": 13.0827, "lon": 80.2707, "label": "Chennai"}),
        ("glide_to_telemetry", {}),
        ("jarvis_play_sfx", {"effect": "target_lock"}),
        ("set_operator_salutation", {"salutation": "Sir"}),
        ("toggle_tactical_layer", {"layer": "flights", "state": True}),
    ]

    for ev_name, payload in test_events:
        bus.emit(ev_name, payload)

    dispatched_names = [d[0] for d in dispatched]

    for ev_name, _ in test_events:
        assert dispatched_names.count(ev_name) == 1, f"Event {ev_name} was dispatched {dispatched_names.count(ev_name)} times"

    # Ensure none were duplicated as jarvis_task_event
    assert "jarvis_task_event" not in dispatched_names
