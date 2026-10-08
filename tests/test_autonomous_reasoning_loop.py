import pytest
from core.jarvis_reasoning_loop import JarvisCognitiveLoop
from core.agent_router import AgentRouter
def test_cognitive_loop_goal_decomposition():
    loop = JarvisCognitiveLoop()
    prompt = "Check weather in Mumbai and see how traffic is flowing on the sea link"
    steps = loop.analyze_goal(prompt)
    
    actions = [s["action"] for s in steps]
    assert "nav" in actions
    assert "weather" in actions
    assert "traffic" in actions
    
    for s in steps:
        assert "progress_phrase" in s
        assert len(s["progress_phrase"]) > 5


def test_cognitive_loop_real_time_spoken_progression():
    loop = JarvisCognitiveLoop()
    spoken_messages = []
    ui_messages = []

    def _speak_cb(phrase):
        spoken_messages.append(phrase)

    def _ui_cb(msg):
        ui_messages.append(msg)

    prompt = "Check the weather in Mumbai and see how traffic is flowing"
    result = loop.execute_plan(prompt, on_progress_speak=_speak_cb, on_progress_ui=_ui_cb)

    assert result["handled"] is True
    assert len(spoken_messages) >= 2
    assert any("Mumbai" in s for s in spoken_messages)
    assert "All operational tasks completed, Sir" in result["text"]
    assert "Mumbai" in result["text"]
    assert len(result["observations"]) >= 2


def test_no_hardcoded_location_fallbacks():
    router = AgentRouter()
    
    # 1. Weather without location -> asks user rather than defaulting to Kotagiri
    handled, ack, task, cat = router.route_input("what's the weather?")
    assert handled is True
    assert task is None
    assert "Which city or region would you like atmospheric telemetry for" in ack

    # 2. Traffic without location -> asks user rather than defaulting
    handled, ack, task, cat = router.route_input("how is the traffic?")
    assert handled is True
    assert task is None
    assert "Which city or sector would you like live traffic telemetry for" in ack

    # 3. Weather engine resolve_location with empty query returns None
    from modules.weather_intel import WeatherIntelEngine
    we = WeatherIntelEngine()
    assert we.resolve_location("") is None
    assert we.get_weather("") is None

    # 4. Cognitive loop requests location when omitted
    loop = JarvisCognitiveLoop()
    res = loop.execute_plan("what's the weather?")
    assert res["handled"] is True
    assert "Which city or region" in res["text"]


def test_frontend_gods_eye_flight_parity():
    with open("frontend/app.html", "r") as f:
        html = f.read()

    # 1. Official God's Eye 3D GLB model integration with Level of Detail (LOD)
    assert "/models/jet.glb" in html
    assert "DistanceDisplayCondition" in html
    assert "seedContingencyFlight" not in html
    assert "VIPER-11" not in html

    # 2. Initial Cesium camera is global overview, not Kotagiri coordinates
    assert "Cesium.Cartesian3.fromDegrees(76.8661, 11.4228, 22000000)" not in html
    assert "Cesium.Cartesian3.fromDegrees(0.0, 20.0, 25000000)" in html

    # 3. No startup Kotagiri weather poll
    assert "updateSectorWeather(11.4228, 76.8661, 'Kotagiri')" not in html
