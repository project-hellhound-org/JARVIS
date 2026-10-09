# tests/test_maritime_intel.py
"""
Unit tests for Maritime AIS telemetry client and AISStream integration.
Validates:
1. AIS key not configured status when key is absent.
2. Contingency fleet returned when demo mode is active.
3. AISStream client configuration detection.
"""

import os
from unittest.mock import patch
from modules.maritime_intel import (
    MaritimeIntelClient,
    AISStreamClient,
    get_maritime_client,
    get_aisstream_api_key
)


def test_aisstream_key_absent_returns_not_configured():
    with patch.dict(os.environ, {"AISSTREAM_API_KEY": ""}, clear=False):
        client = MaritimeIntelClient()
        client.ais_client.api_key = ""
        res = client.get_vessels_in_area(11.0, 80.0, radius_km=100)
        assert res["key_configured"] is False
        assert res["total"] == 0
        assert len(res["vessels"]) == 0
        assert "AIS key not configured" in res["message"]


def test_aisstream_demo_mode_returns_contingency():
    with patch.dict(os.environ, {"AISSTREAM_API_KEY": ""}, clear=False):
        client = MaritimeIntelClient()
        client.ais_client.api_key = ""
        res = client.get_vessels_in_area(12.8, 80.4, radius_km=300, demo_mode=True)
        assert res["key_configured"] is False
        assert res["freshness"] == "simulated"
        assert res["total"] > 0
        assert len(res["vessels"]) > 0
        names = [v["name"] for v in res["vessels"]]
        assert any("INS VIKRANT" in n for n in names)


def test_aisstream_is_configured_with_valid_key():
    client = AISStreamClient(api_key="valid_test_aisstream_key_12345")
    assert client.is_configured() is True
