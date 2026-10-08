import json
import pytest
from unittest.mock import patch, MagicMock
import bottle
from modules.osiris_intel import get_osiris_client, OsirisIntelClient
from frontend.desktop import setup_jarvis_bottle_routes, JarvisAPI


def test_satellite_default_limit_is_1500():
    client = get_osiris_client()
    with patch.object(client, "_fetch_endpoint", return_value=None):
        res = client.get_satellites(return_meta=True)
        assert res["limit"] == 1500
        assert res["status"] == "upstream_error"
        # Contingency ephemeris returns multi-orbit fixtures
        assert len(res["satellites"]) >= 10
        categories = {s.get("category") for s in res["satellites"]}
        assert "stations" in categories
        assert "nav" in categories
        assert "geo" in categories
        assert "visual" in categories
        assert "starlink" in categories


def test_bottle_route_satellite_limit_defaults_to_1500():
    app = bottle.Bottle()
    api = JarvisAPI()
    setup_jarvis_bottle_routes(app, "/tmp", api=api)

    with patch.object(OsirisIntelClient, "_fetch_endpoint", return_value={"satellites": [{"name": f"SAT-{i}", "lat": 0, "lng": 0, "alt": 500} for i in range(1200)]}):
        env = {
            'PATH_INFO': '/api/satellites/active',
            'REQUEST_METHOD': 'GET',
            'QUERY_STRING': 'category=all'
        }
        res = app._handle(env)
        data = json.loads(res)
        assert data["status"] == "ok"
        assert data["limit"] == 1500
        assert data["count"] == 1200


def test_app_html_satellite_constellation_architecture():
    with open("frontend/app.html", "r", encoding="utf-8") as f:
        html = f.read()

    # 1. PointPrimitiveCollection utilized for WebGL single-draw-call 60 FPS scaling
    assert "new Cesium.PointPrimitiveCollection()" in html
    assert "_satellitePoints" in html

    # 2. God's Eye satelliteClass taxonomy palette
    assert "#fff6e5" in html  # STATION (warm white)
    assert "#4fd8ff" in html  # NAV (cyan)
    assert "#c89bff" in html  # GEO (violet)
    assert "#9fb3c4" in html  # VISUAL (muted blue-gray)
    assert "#54697f" in html  # COMMS/STARLINK (dim slate)

    # 3. Focused single nadir line and ground target
    assert "setFocusedSatelliteTrack" in html
    assert "clearFocusedSatelliteTrack" in html
    assert "focused-sat-nadir" in html
    assert "focused-sat-ground-target" in html

    # 4. PointPrimitive click picking in Cesium ScreenSpaceEventHandler
    assert "picked.primitive && picked.primitive._tacticalData" in html

    # 5. Authentic God's Eye Header layout for satellite inspection
    assert "NAME · ALT · VELOCITY" in html or "data.velocityKmh" in html
    assert "TRACK SATELLITE" in html
