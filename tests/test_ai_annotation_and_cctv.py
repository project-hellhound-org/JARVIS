import pytest
import re
from pathlib import Path
from unittest.mock import MagicMock
from core.jarvis_reasoning_loop import JarvisCognitiveLoop
from core.agent_router import AgentRouter


def test_reasoning_loop_annotate_tool():
    """Verify JarvisCognitiveLoop tool_annotate_area calculates coordinates and emits event."""
    bus = MagicMock()
    loop = JarvisCognitiveLoop(event_bus=bus)

    res = loop.tool_annotate_area(
        location_or_target="Kotagiri",
        sector_name="ALPHA DEFENSE SECTOR",
        radius_km=45.0,
        classification="DEFENSE ZONE"
    )

    assert res["success"] is True
    assert "ALPHA DEFENSE SECTOR" in res["debrief"]
    assert res["radius_km"] == 45.0
    assert abs(res["lat"] - 11.4228) < 0.05
    assert abs(res["lon"] - 76.8661) < 0.05

    # Check EventBus emit
    bus.emit.assert_any_call("annotate_area", {
        "lat": res["lat"],
        "lon": res["lon"],
        "radius_km": 45.0,
        "sector_name": "ALPHA DEFENSE SECTOR",
        "classification": "DEFENSE ZONE",
        "use_camera_center": False
    })


def test_reasoning_loop_analyze_goal_annotate():
    """Verify autonomous intent detection for area annotation requests."""
    bus = MagicMock()
    loop = JarvisCognitiveLoop(event_bus=bus)

    steps = loop.analyze_goal("annotate this area with a 50km defense zone")
    assert isinstance(steps, list)
    assert len(steps) >= 1
    assert steps[0]["action"] == "annotate"
    assert steps[0]["radius_km"] == 50.0
    assert steps[0]["classification"] == "DEFENSE ZONE"


def test_agent_router_annotate_area_intent():
    """Verify AgentRouter routes annotate area requests and emits across EventBus."""
    bus = MagicMock()
    tm = MagicMock()
    tm.create_task.return_value = {"id": "task_1", "type": "annotate_area"}
    router = AgentRouter()
    router.event_bus = bus
    router.task_manager = tm

    handled, phrase, task, action = router.route_input("mark a 30km no-fly zone around this sector")
    assert handled is True
    assert action == "annotate_area"
    assert "Illuminating tactical perimeter" in phrase
    bus.emit.assert_any_call("annotate_area", {
        "sector_name": "NO-FLY ZONE",
        "radius_km": 30.0,
        "classification": "NO-FLY ZONE",
        "use_camera_center": True
    })


def test_frontend_app_html_features():
    """Verify frontend components in app.html: depth testing, AI sectors."""
    html_path = Path("/home/joe/Project-Hellhound/JARVIS/frontend/app.html")
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")

    # 1. Horizon depth testing against terrain
    assert "scene.globe.depthTestAgainstTerrain = true;" in content
    # No disableDepthTestDistance with POSITIVE_INFINITY depth bypass remains
    assert "disableDepthTestDistance: Number.POSITIVE_INFINITY" not in content

    # 2. AI active sectors HUD overlay
    assert 'id="globe-active-sectors-pill"' in content
    assert 'id="globe-active-sectors-dropdown"' in content
    assert "window.annotateCurrentArea" in content
    assert "window.toggleTacticalSectorsDropdown" in content
    assert "case 'annotate_area':" in content
