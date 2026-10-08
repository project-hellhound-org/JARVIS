# tests/test_osiris_fallback.py
"""
Test Suite for OSIRIS Upstream Error Fallback and Clean Recovery.

Validates:
1. Upstream failure simulation via unreachable host (port 9999) or URLError mock.
2. Bridge methods (get_active_satellites, get_active_conflicts) under upstream failure:
   - Return status: "upstream_error"
   - Debrief message is populated
   - Contingency data is non-empty (local fixtures / orbital ephemeris)
   - Do not raise unhandled exceptions
3. Clean recovery mechanism:
   - Once connection is restored, next poll returns status: "ok" with fresh live data
   - Does NOT get stuck showing stale contingency data
4. Bottle endpoints under upstream error return structured JSON with status: "upstream_error"
"""

import json
import urllib.error
import urllib.request
from unittest.mock import patch, MagicMock
import pytest
import bottle

from modules.osiris_intel import OsirisIntelClient, get_osiris_client
from frontend.desktop import JarvisAPI, setup_jarvis_bottle_routes


MOCK_LIVE_SATELLITES = {
    "total": 2,
    "satellites": [
        {
            "name": "LIVE-ORBIT-SAT-1",
            "lat": 10.0,
            "lng": 20.0,
            "alt": 550,
            "mission": "Live Tactical Recon",
            "category": "recon",
            "noradId": "99001",
        }
    ]
}

MOCK_LIVE_CONFLICTS = {
    "totalZones": 1,
    "activeWarzones": 1,
    "zones": [
        {
            "id": "live-theater-alpha",
            "label": "LIVE THEATER ALPHA",
            "severity": "war",
            "lat": 30.0,
            "lng": 40.0,
            "description": "Live active combat zone.",
            "status": "active"
        }
    ],
    "liveEvents": []
}



class MockHTTPResponse:
    def __init__(self, data_dict):
        self.data_bytes = json.dumps(data_dict).encode("utf-8")

    def read(self):
        return self.data_bytes

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def test_upstream_error_fallback_and_clean_recovery():
    """
    Simulate upstream HTTP failure (URLError: Connection refused) and verify:
    1. get_active_satellites returns upstream_error + contingency ephemeris.
    2. get_active_conflicts returns upstream_error + contingency zones.
    3. Upon network restoration, subsequent calls recover immediately to status: 'ok'.
    """
    api = JarvisAPI()
    client = get_osiris_client()
    client.clear_cache()

    # Step 1: Simulate upstream failure using URLError
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        # Satellites fallback
        sat_err = api.get_active_satellites(category="all", limit=60)
        assert sat_err["status"] == "upstream_error"
        assert len(sat_err["satellites"]) > 0
        assert any("ISS" in s["name"] for s in sat_err["satellites"])
        assert "offline" in sat_err["debrief"].lower() or "contingency" in sat_err["debrief"].lower()

        # Conflicts fallback
        conf_err = api.get_active_conflicts()
        assert conf_err["status"] == "upstream_error"
        assert len(conf_err["zones"]) > 0
        assert any("UKRAINE" in z["label"] for z in conf_err["zones"])
        assert "offline" in conf_err["debrief"].lower() or "contingency" in conf_err["debrief"].lower()

    # Step 2: Remove failure simulation and verify clean recovery
    client.clear_cache()
    with patch("urllib.request.urlopen") as mock_url:
        # Mock restored satellite response
        mock_url.return_value = MockHTTPResponse(MOCK_LIVE_SATELLITES)
        sat_ok = api.get_active_satellites(category="recon", limit=60)
        assert sat_ok["status"] == "ok"
        assert sat_ok["count"] == 1
        assert sat_ok["satellites"][0]["name"] == "LIVE-ORBIT-SAT-1"
        assert "active" in sat_ok["debrief"].lower()

        # Mock restored conflicts response
        mock_url.return_value = MockHTTPResponse(MOCK_LIVE_CONFLICTS)
        conf_ok = api.get_active_conflicts()
        assert conf_ok["status"] == "ok"
        assert len(conf_ok["zones"]) == 1
        assert conf_ok["zones"][0]["id"] == "live-theater-alpha"
        assert "active" in conf_ok["debrief"].lower()


def test_unreachable_host_simulation():
    """
    Test failure simulation via client.base_url redirection to a closed local port (9999).
    Confirms that real network layer cleanly rejects the connection and returns contingency data.
    """
    client = get_osiris_client()
    original_url = client.base_url
    client.clear_cache()

    try:
        # Point to closed local port -> triggers real URLError
        client.base_url = "http://127.0.0.1:9999"

        sats = client.get_satellites(return_meta=True)
        assert sats["status"] == "upstream_error"
        assert len(sats["satellites"]) >= 2
        assert "contingency" in sats["debrief"].lower()

        conf = client.get_conflicts(return_meta=True)
        assert conf["status"] == "upstream_error"
        assert len(conf["zones"]) >= 1

    finally:
        client.base_url = original_url
        client.clear_cache()


def test_bottle_routes_under_upstream_failure():
    """
    Confirm Bottle HTTP endpoints return 200 with structured JSON describing upstream_error
    so frontend webview does not receive unhandled HTTP 500s.
    """
    app = bottle.Bottle()
    api = JarvisAPI()
    setup_jarvis_bottle_routes(app, "/tmp", api=api)
    client = get_osiris_client()
    client.clear_cache()

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        # Satellites route
        env_sat = {'PATH_INFO': '/api/satellites/active', 'REQUEST_METHOD': 'GET', 'QUERY_STRING': 'category=all'}
        out_sat = json.loads(app._handle(env_sat))
        assert out_sat["status"] == "upstream_error"
        assert len(out_sat["satellites"]) > 0

        # Conflicts route
        env_conf = {'PATH_INFO': '/api/conflicts/active', 'REQUEST_METHOD': 'GET', 'QUERY_STRING': ''}
        out_conf = json.loads(app._handle(env_conf))
        assert out_conf["status"] == "upstream_error"
        assert len(out_conf["zones"]) > 0
