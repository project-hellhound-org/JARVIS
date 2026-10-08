import pytest
from core.jarvis_reasoning_loop import JarvisCognitiveLoop
from core.task import TaskType
from core.event_bus import get_event_bus

def test_analyze_goal_viewport_annotation():
    loop = JarvisCognitiveLoop()
    prompt = "J.A.R.V.I.S., I am telling you to annotate Donut SK in this place which I am seeing"
    steps = loop.analyze_goal(prompt)
    assert len(steps) == 1
    assert steps[0]["action"] == "annotate"
    assert steps[0]["sector_name"] == "Donut SK"
    assert steps[0]["location"] == ""

def test_tool_annotate_area_emits_telemetry_and_camera_center():
    bus = get_event_bus()
    emitted = []
    def _listener(data):
        emitted.append(data)

    bus.subscribe("annotate_area", _listener)
    bus.subscribe("glide_to_telemetry", _listener)

    loop = JarvisCognitiveLoop()
    res = loop.tool_annotate_area(location_or_target="", sector_name="Donut SK", radius_km=25.0)

    assert res["success"] is True
    assert res["sector_name"] == "Donut SK"
    assert res["lat"] is None
    assert res["lon"] is None
    assert "optical vantage" in res["debrief"]

    ann_events = [e for e in emitted if isinstance(e, dict) and e.get("sector_name") == "Donut SK"]
    assert len(ann_events) >= 1
    assert ann_events[0]["use_camera_center"] is True

def test_flight_query_and_telemetry():
    bus = get_event_bus()
    telemetry_events = []
    bus.subscribe("glide_to_telemetry", lambda d: telemetry_events.append(d))

    loop = JarvisCognitiveLoop()
    steps = loop.analyze_goal("show me the nearest flight")
    actions = [s["action"] for s in steps]
    assert "flights" in actions
    assert "cockpit" in actions

    f_res = loop.tool_flight_radar()
    assert f_res["success"] is True
    assert len(telemetry_events) >= 1

def test_tactical_goal_task_type():
    loop = JarvisCognitiveLoop()
    res = loop.execute_plan("annotate Donut SK in this place which I am seeing")
    assert res["handled"] is True
    task_id = res["task_id"]
    task = loop.task_manager.get_task(task_id)
    assert task is not None
    assert task.type == TaskType.TACTICAL_GOAL.value
