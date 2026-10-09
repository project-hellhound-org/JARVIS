# tests/test_osiris_phase2.py
"""
Test Suite for OSIRIS Phase 2: Native Telemetry Ingestion, Viewport Filtering, and Bridge Methods.
Validates:
1. parse_bounds & is_point_in_bounds geometry functions (including antimeridian handling).
3. Flight bounding-box filtering, category filtering, display capping, 0-results, and upstream error fallbacks.
4. Satellite category filtering (ISS, Tiangong, GPS, Starlink, Recon), bounding-box filtering, and fallbacks.
5. Conflict bounding-box filtering, severity filtering, and fallbacks.
6. JarvisAPI bridge methods: get_active_satellites, get_active_conflicts.
7. Bottle API routes: /api/satellites/active, /api/conflicts/active.
"""

import pytest
import json
from unittest.mock import patch, MagicMock
from modules.osiris_intel import (
    OsirisIntelClient,
    get_osiris_client,
    parse_bounds,
    is_point_in_bounds,
)
from frontend.desktop import JarvisAPI, setup_jarvis_bottle_routes
import bottle



SAMPLE_FLIGHTS_DATA = {
    "total": 4,
    "military_flights": [
        {"callsign": "VIPER11", "lat": 37.8, "lng": -122.4, "category": "military", "model": "F-16"},
        {"callsign": "REAPER01", "lat": 32.5, "lng": 35.0, "category": "military", "model": "MQ-9"},
    ],
    "commercial_flights": [
        {"callsign": "UAL420", "lat": 37.6, "lng": -122.3, "category": "commercial", "model": "B737"},
    ],
    "private_flights": [
        {"callsign": "N12345", "lat": 51.5, "lng": -0.1, "category": "private", "model": "GLF6"},
    ],
    "gps_jamming": [
        {"lat": 33.0, "lng": 35.5, "radius_km": 50, "severity": "HIGH"},
    ],
}

SAMPLE_SATELLITE_CATALOG = {
    "total": 5,
    "satellites": [
        {
            "name": "ISS (ZARYA)",
            "lat": 37.5,
            "lng": -122.0,
            "alt": 420,
            "mission": "Space Station",
            "category": "space_station",
            "noradId": "25544",
        },
        {
            "name": "TIANGONG (CSS TIANHE)",
            "lat": 15.0,
            "lng": 115.0,
            "alt": 390,
            "mission": "Chinese Space Station",
            "category": "space_station",
            "noradId": "48274",
        },
        {
            "name": "NAVSTAR GPS 74 (USA-248)",
            "lat": 38.0,
            "lng": -122.2,
            "alt": 20200,
            "mission": "Navigation Constellation",
            "category": "navigation",
            "noradId": "39533",
        },
        {
            "name": "STARLINK-30124",
            "lat": 37.9,
            "lng": -122.1,
            "alt": 550,
            "mission": "Broadband Internet",
            "category": "starlink",
            "noradId": "52001",
        },
        {
            "name": "USA-224 (KH-11 KENNEN)",
            "lat": 37.7,
            "lng": -122.3,
            "alt": 260,
            "mission": "Reconnaissance Optical",
            "category": "military",
            "noradId": "37348",
        },
    ]
}

SAMPLE_CONFLICTS_DATA = {
    "totalZones": 10,
    "activeWarzones": 4,
    "zones": [
        {
            "id": "donbas-sector",
            "label": "EASTERN UKRAINE THEATRE",
            "severity": "war",
            "lat": 48.0,
            "lng": 37.8,
            "description": "Intense mechanized artillery combat.",
        },
        {
            "id": "red-sea-corridor",
            "label": "RED SEA MARITIME CHOKEPOINT",
            "severity": "crisis",
            "lat": 13.5,
            "lng": 42.8,
            "description": "Anti-ship ballistic missile and drone interdictions.",
        },
    ],
    "liveEvents": [
        {"id": "ev-1", "lat": 48.1, "lng": 37.9, "title": "Missile Impact Strike", "severity": "war"},
    ]
}


# =============================================================================
# 1. Bounds Parsing and Point-In-Bounds Tests
# =============================================================================

def test_parse_bounds_formats():
    # min/max format
    b1 = parse_bounds({"min_lat": 30.0, "min_lon": -120.0, "max_lat": 40.0, "max_lon": -110.0})
    assert b1 == (30.0, -120.0, 40.0, -110.0)

    # south/west/north/east format (Cesium standard)
    b2 = parse_bounds({"south": 10.5, "west": 70.0, "north": 20.5, "east": 80.0})
    assert b2 == (10.5, 70.0, 20.5, 80.0)

    # list / tuple format
    b3 = parse_bounds([12.0, 75.0, 15.0, 78.0])
    assert b3 == (12.0, 75.0, 15.0, 78.0)

    # Invalid / empty
    assert parse_bounds(None) is None
    assert parse_bounds({}) is None
    assert parse_bounds("invalid") is None


def test_is_point_in_bounds():
    bounds = (30.0, -125.0, 45.0, -115.0)

    # Inside
    assert is_point_in_bounds(37.77, -122.41, bounds) is True
    # Outside latitude
    assert is_point_in_bounds(50.0, -120.0, bounds) is False
    # Outside longitude
    assert is_point_in_bounds(35.0, -100.0, bounds) is False
    # None coordinates
    assert is_point_in_bounds(None, -120.0, bounds) is False
    assert is_point_in_bounds(35.0, None, bounds) is False

    # Antimeridian crossing (+170 to -170, i.e. min_lon > max_lon)
    antimeridian_bounds = (-10.0, 170.0, 10.0, -170.0)
    assert is_point_in_bounds(0.0, 175.0, antimeridian_bounds) is True
    assert is_point_in_bounds(0.0, -175.0, antimeridian_bounds) is True
    assert is_point_in_bounds(0.0, 0.0, antimeridian_bounds) is False




# =============================================================================
# 3. Flight Telemetry Viewport & Category Tests
# =============================================================================

@patch.object(OsirisIntelClient, "_fetch_endpoint")
def test_flights_viewport_and_category_filtering(mock_fetch):
    mock_fetch.return_value = SAMPLE_FLIGHTS_DATA
    client = get_osiris_client()

    # Bounding box over California
    ca_bounds = {"south": 35.0, "west": -125.0, "north": 40.0, "east": -120.0}
    fls = client.get_flights(bounds=ca_bounds)
    assert fls["status"] == "ok"
    assert len(fls["military"]) == 1
    assert fls["military"][0]["callsign"] == "VIPER11"
    assert len(fls["commercial"]) == 1
    assert fls["commercial"][0]["callsign"] == "UAL420"
    assert len(fls["private"]) == 0
    assert len(fls["gps_jamming"]) == 0

    # Category filter: military only
    fls_mil = client.get_flights(category="military")
    assert len(fls_mil["military"]) == 2
    assert len(fls_mil["commercial"]) == 0
    assert len(fls_mil["private"]) == 0

    # Zero results in empty viewport
    empty_bounds = {"south": -80.0, "west": 0.0, "north": -75.0, "east": 10.0}
    fls_empty = client.get_flights(bounds=empty_bounds)
    assert fls_empty["status"] == "zero_results"
    assert fls_empty["total"] == 0

    # Upstream error fallback
    mock_fetch.return_value = None
    fls_err = client.get_flights()
    assert fls_err["status"] in ("upstream_error", "ok")
    assert isinstance(fls_err["military"], list)


# =============================================================================
# 4. Satellite Constellation Category and Viewport Tests
# =============================================================================

@patch.object(OsirisIntelClient, "_fetch_endpoint")
def test_satellite_operator_categories(mock_fetch):
    mock_fetch.return_value = SAMPLE_SATELLITE_CATALOG
    client = get_osiris_client()

    # 1. ISS
    iss_sats = client.get_satellites(category="iss")
    assert len(iss_sats) == 1
    assert iss_sats[0]["noradId"] == "25544"

    # 2. Tiangong
    tg_sats = client.get_satellites(category="tiangong")
    assert len(tg_sats) == 1
    assert tg_sats[0]["noradId"] == "48274"

    # 3. GPS
    gps_sats = client.get_satellites(category="gps")
    assert len(gps_sats) == 1
    assert gps_sats[0]["noradId"] == "39533"

    # 4. Starlink
    sl_sats = client.get_satellites(category="starlink")
    assert len(sl_sats) == 1
    assert sl_sats[0]["noradId"] == "52001"

    # 5. Recon
    recon_sats = client.get_satellites(category="recon")
    assert len(recon_sats) == 1
    assert recon_sats[0]["noradId"] == "37348"


@patch.object(OsirisIntelClient, "_fetch_endpoint")
def test_satellite_viewport_and_fallbacks(mock_fetch):
    mock_fetch.return_value = SAMPLE_SATELLITE_CATALOG
    client = get_osiris_client()

    # Sub-satellite point in California
    ca_bounds = {"south": 36.0, "west": -123.0, "north": 39.0, "east": -121.0}
    meta_res = client.get_satellites(bounds=ca_bounds, return_meta=True)
    assert meta_res["status"] == "ok"
    assert meta_res["count"] >= 3  # ISS, GPS, Starlink, KH-11 are in sector

    # Zero results
    south_pole = {"south": -90.0, "west": -10.0, "north": -85.0, "east": 10.0}
    zero_res = client.get_satellites(bounds=south_pole, return_meta=True)
    assert zero_res["status"] == "zero_results"
    assert zero_res["count"] == 0

    # Upstream error fallback
    mock_fetch.return_value = None
    err_res = client.get_satellites(return_meta=True)
    assert err_res["status"] == "upstream_error"
    assert len(err_res["satellites"]) > 0  # Contingency ephemeris returned
    assert "contingency orbital ephemeris" in err_res["debrief"].lower()


# =============================================================================
# 5. Conflict Viewport and Severity Tests
# =============================================================================

@patch.object(OsirisIntelClient, "_fetch_endpoint")
def test_conflicts_filtering_and_fallbacks(mock_fetch):
    mock_fetch.return_value = SAMPLE_CONFLICTS_DATA
    client = get_osiris_client()

    # Ukraine bounding box
    ukr_bounds = {"south": 45.0, "west": 30.0, "north": 52.0, "east": 42.0}
    conf_ukr = client.get_conflicts(bounds=ukr_bounds)
    assert conf_ukr["status"] == "ok"
    assert conf_ukr["totalZones"] == 1
    assert conf_ukr["zones"][0]["id"] == "donbas-sector"
    assert len(conf_ukr["liveEvents"]) == 1

    # Severity filter: crisis only
    conf_crisis = client.get_conflicts(severity="crisis")
    assert conf_crisis["totalZones"] == 1
    assert conf_crisis["zones"][0]["id"] == "red-sea-corridor"

    # Upstream error fallback
    mock_fetch.return_value = None
    conf_err = client.get_conflicts()
    assert conf_err["status"] == "upstream_error"
    assert len(conf_err["zones"]) >= 1


# =============================================================================
# 6. JarvisAPI Bridge Methods Tests
# =============================================================================

@patch.object(OsirisIntelClient, "_fetch_endpoint")
def test_jarvis_api_new_bridge_methods(mock_fetch):
    api = JarvisAPI()

    # 1. get_active_satellites
    mock_fetch.return_value = SAMPLE_SATELLITE_CATALOG
    res_sat = api.get_active_satellites(category="recon", limit=10)
    assert res_sat["status"] == "ok"
    assert res_sat["count"] == 1
    assert res_sat["satellites"][0]["name"] == "USA-224 (KH-11 KENNEN)"

    # 2. get_active_conflicts
    mock_fetch.return_value = SAMPLE_CONFLICTS_DATA
    res_conf = api.get_active_conflicts()
    assert res_conf["status"] == "ok"
    assert res_conf["activeWarzones"] == 4
    assert len(res_conf["zones"]) == 2


def test_jarvis_api_pywebview_bridge_inspect_signatures():
    """
    Ensure every exposed bridge method on JarvisAPI can be inspected via inspect.getfullargspec
    without throwing TypeError or NameError (e.g. from unresolved type annotations in Python 3.14).
    This directly prevents pywebview 'unsupported callable' bridge binding failures at startup.
    """
    import inspect
    api = JarvisAPI()
    failed = []
    for name in dir(api):
        if not name.startswith('_'):
            attr = getattr(api, name)
            if inspect.ismethod(attr) or inspect.isfunction(attr):
                try:
                    spec = inspect.getfullargspec(attr)
                    assert spec is not None
                except Exception as e:
                    failed.append((name, str(e)))
    assert not failed, f"pywebview bridge binding signature failures: {failed}"


# =============================================================================
# 7. Bottle API Routes Tests
# =============================================================================

@patch.object(OsirisIntelClient, "_fetch_endpoint")
def test_bottle_native_telemetry_routes(mock_fetch):
    app = bottle.Bottle()
    api = JarvisAPI()
    setup_jarvis_bottle_routes(app, "/tmp", api=api)

    # Test /api/satellites/active
    mock_fetch.return_value = SAMPLE_SATELLITE_CATALOG
    env_sat = {'PATH_INFO': '/api/satellites/active', 'REQUEST_METHOD': 'GET', 'QUERY_STRING': 'category=iss'}
    out_sat = app._handle(env_sat)
    data_sat = json.loads(out_sat)
    assert data_sat["status"] == "ok"
    assert data_sat["count"] == 1
    assert data_sat["satellites"][0]["noradId"] == "25544"

    # Test /api/conflicts/active
    mock_fetch.return_value = SAMPLE_CONFLICTS_DATA
    env_conf = {'PATH_INFO': '/api/conflicts/active', 'REQUEST_METHOD': 'GET', 'QUERY_STRING': ''}
    out_conf = app._handle(env_conf)
    data_conf = json.loads(out_conf)
    assert data_conf["status"] == "ok"
    assert data_conf["activeWarzones"] == 4
