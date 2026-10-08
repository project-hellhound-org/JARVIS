# tests/test_osiris_integration.py
import pytest
from unittest.mock import patch, MagicMock
from modules.osiris_intel import OsirisIntelClient, get_osiris_client
from modules.flight_intel import FlightIntelEngine
from core.agent_router import AgentRouter
from core.system_skills import SystemSkillEngine
from core.jarvis_reasoning_loop import JarvisCognitiveLoop
from frontend.desktop import JarvisAPI

SAMPLE_OSIRIS_FLIGHTS = {
    "total": 10,
    "military_flights": [
        {
            "callsign": "VIPER21",
            "lat": 35.1,
            "lng": 24.2,
            "alt": 28000,
            "heading": 120,
            "speed_knots": 480.0,
            "model": "F-16C Fighting Falcon",
            "icao24": "ae01ff",
            "registration": "91-0352",
            "squawk": "4211",
            "category": "military"
        }
    ],
    "commercial_flights": [
        {
            "callsign": "BAW123",
            "lat": 51.4,
            "lng": -0.4,
            "alt": 32000,
            "heading": 270,
            "speed_knots": 450.0,
            "model": "A320",
            "icao24": "4002ab",
            "registration": "G-EUUI",
            "squawk": "1200",
            "category": "commercial"
        }
    ],
    "gps_jamming": [
        {"lat": 34.5, "lng": 35.5, "radius_km": 80, "severity": "HIGH"}
    ]
}

SAMPLE_OSIRIS_SATS = {
    "total": 2,
    "satellites": [
        {
            "name": "ISS (ZARYA)",
            "lat": -15.42,
            "lng": 102.15,
            "alt": 418,
            "mission": "Space Station",
            "category": "space_station",
            "noradId": "25544"
        }
    ]
}

SAMPLE_OSIRIS_CONFLICTS = {
    "totalZones": 15,
    "activeWarzones": 6,
    "zones": [
        {
            "id": "ukraine",
            "label": "UKRAINE WAR",
            "severity": "war",
            "lat": 48.5,
            "lng": 31.2,
            "description": "Ongoing conflict — active frontlines across eastern regions."
        }
    ]
}


def test_osiris_intel_client_singleton():
    c1 = get_osiris_client()
    c2 = get_osiris_client()
    assert c1 is c2
    assert c1.base_url.startswith("http")


def test_osiris_haversine():
    # Distance between London (51.5074, -0.1278) and Paris (48.8566, 2.3522) is approx 343 km
    dist = OsirisIntelClient.haversine_distance(51.5074, -0.1278, 48.8566, 2.3522)
    assert 340 <= dist <= 346




@patch.object(OsirisIntelClient, "_fetch_endpoint")
def test_osiris_flights_and_satellites(mock_fetch):
    client = get_osiris_client()

    mock_fetch.return_value = SAMPLE_OSIRIS_FLIGHTS
    flights = client.get_flights(military_only=True)
    assert flights["total"] == 1
    assert len(flights["military"]) == 1
    assert flights["military"][0]["callsign"] == "VIPER21"

    mock_fetch.return_value = SAMPLE_OSIRIS_SATS
    sats = client.get_satellites(query="ISS")
    assert len(sats) == 1
    assert sats[0]["noradId"] == "25544"




@patch.object(OsirisIntelClient, "_fetch_endpoint")
def test_flight_intel_discovers_osiris(mock_fetch):
    mock_fetch.return_value = SAMPLE_OSIRIS_FLIGHTS
    engine = FlightIntelEngine()
    engine._cached_flights = []
    engine._last_fetch_time = 0
    with patch("urllib.request.urlopen", side_effect=Exception("Offline")):
        aircraft = engine.get_military_aircraft(limit=10)
        assert len(aircraft) >= 1
        assert any(a.get("flight") == "VIPER21" for a in aircraft)


def test_agent_router_osiris_intents():
    router = AgentRouter()

    # Satellite tracking
    handled, ack, task, action = router.route_input("track satellite ISS")
    assert handled is True
    assert action == "satellite_track"
    assert task.type == "satellite_track"

    # Conflict intelligence
    handled, ack, task, action = router.route_input("give me a warzone briefing on active conflicts")
    assert handled is True
    assert action == "conflict_intel"
    assert task.type == "conflict_intel"

    # Turn-by-turn directions
    handled, ack, task, action = router.route_input("turn by turn route from 'Jamaica' to 'Brooklyn'")
    assert handled is True
    assert action == "directions"
    assert task.type == "directions"

    # Cyber recon
    handled, ack, task, action = router.route_input("cyber recon target domain.com")
    assert handled is True
    assert action == "cyber_recon"
    assert task.type == "cyber_recon"

    # Live news
    handled, ack, task, action = router.route_input("show live news streams")
    assert handled is True
    assert action == "live_news"
    assert task.type == "live_news"


@patch.object(OsirisIntelClient, "_fetch_endpoint")
def test_system_skills_osiris_execution(mock_fetch):
    skills = SystemSkillEngine()

    # 1. Satellite execution
    mock_fetch.return_value = SAMPLE_OSIRIS_SATS
    handled, debrief, _, _, payload = skills.try_execute("track satellite ISS")
    assert handled is True
    assert "ISS (ZARYA)" in debrief
    assert payload.get("action_type") == "SATELLITE"

    # 2. Conflict execution
    mock_fetch.return_value = SAMPLE_OSIRIS_CONFLICTS
    handled, debrief, _, _, payload = skills.try_execute("show conflict zones")
    assert handled is True
    assert "active warzones" in debrief.lower()
    assert payload.get("action_type") == "CONFLICT"


@patch.object(OsirisIntelClient, "_fetch_endpoint")
def test_reasoning_loop_osiris_tools(mock_fetch):
    loop = JarvisCognitiveLoop()

    mock_fetch.return_value = SAMPLE_OSIRIS_SATS
    res_sat = loop.tool_osiris_satellites("ISS")
    assert res_sat["success"] is True
    assert res_sat["name"] == "ISS (ZARYA)"

    mock_fetch.return_value = SAMPLE_OSIRIS_CONFLICTS
    res_conf = loop.tool_osiris_conflicts()
    assert res_conf["success"] is True
    assert res_conf["activeWarzones"] == 6


@patch.object(OsirisIntelClient, "_fetch_endpoint")
def test_jarvis_api_osiris_methods(mock_fetch):
    api = JarvisAPI()

    mock_fetch.return_value = {"stats": {"flights": 5000, "sats": 18000, "radar": 28000}}
    stats = api.get_osiris_stats()
    assert stats.get("flights") == 5000
    mock_fetch.return_value = SAMPLE_OSIRIS_FLIGHTS
    fls = api.get_osiris_flights(military_only=True)
    assert len(fls.get("military", [])) == 1


def test_bottle_osiris_proxy_routes_and_header_stripping():
    import bottle
    from unittest.mock import MagicMock

    app = bottle.Bottle()

    # Simulate our proxy handler
    def fake_fetch_osiris(path, method="GET", body=None, content_type=None):
        html_body = b"<!DOCTYPE html><html><head><title>OSIRIS</title></head><body><div id='root'></div></body></html>"
        headers = {
            "Content-Type": "text/html; charset=utf-8",
            "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": "default-src 'self'",
            "Strict-Transport-Security": "max-age=31536000"
        }
        return 200, headers, html_body

    def fake_serve_osiris(path, method="GET"):
        status, headers, data = fake_fetch_osiris(path, method=method)
        bottle.response.status = status
        for k, v in headers.items():
            bottle.response.set_header(k, v)
        bottle.response.set_header('Access-Control-Allow-Origin', '*')
        for rm in ['X-Frame-Options', 'Content-Security-Policy', 'Content-Security-Policy-Report-Only', 'Strict-Transport-Security']:
            if rm in bottle.response.headers:
                del bottle.response.headers[rm]
        return data

    @app.route('/osiris-live')
    def osiris_live_route():
        status, headers, data = fake_fetch_osiris('/', method='GET')
        html = data.decode('utf-8')
        bootstrap = '<script>window.__JARVIS_EMBEDDED_OSIRIS__=true;</script>'
        html = html.replace('<head>', f'<head>{bootstrap}', 1)
        bottle.response.status = status
        bottle.response.content_type = 'text/html; charset=utf-8'
        bottle.response.set_header('Access-Control-Allow-Origin', '*')
        for rm in ['X-Frame-Options', 'Content-Security-Policy', 'Content-Security-Policy-Report-Only', 'Strict-Transport-Security']:
            if rm in bottle.response.headers:
                del bottle.response.headers[rm]
        return html.encode('utf-8')

    # Test route invocation
    env = {'PATH_INFO': '/osiris-live', 'REQUEST_METHOD': 'GET'}
    result = app._handle(env)
    assert b'window.__JARVIS_EMBEDDED_OSIRIS__=true' in result
    assert 'X-Frame-Options' not in bottle.response.headers
    assert 'Content-Security-Policy' not in bottle.response.headers


def test_app_html_native_osiris_layer_and_no_iframe():
    with open('frontend/app.html', 'r', encoding='utf-8') as f:
        html = f.read()

    # Verify osirisContainer iframe and /osiris-live proxy are completely removed
    assert 'id="osirisContainer"' not in html
    assert '/osiris-live' not in html

    # Verify native Cesium integration and HUD elements are present
    assert 'setGlobeEngine' in html
    assert 'OsirisCesiumLayer' in html
    assert 'osiris-telemetry-debrief' in html

