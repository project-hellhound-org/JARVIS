# tests/test_ground_intel_real.py
import pytest
from unittest.mock import patch
from modules.ground_intel import (
    GroundIntelClient,
    get_ground_intel_client,
    haversine_distance,
)

def test_haversine_distance():
    dist = haversine_distance(11.0168, 76.9558, 13.0827, 80.2707)
    assert 400 < dist < 450

def test_empty_results_payload():
    client = GroundIntelClient()
    with patch("modules.ground_intel.fetch_wikimedia_geosearch", return_value=[]), \
         patch("modules.ground_intel.fetch_usgs_earthquakes", return_value=[]), \
         patch("modules.ground_intel.fetch_nasa_eonet", return_value=[]), \
         patch("modules.ground_intel.fetch_gdacs_alerts", return_value=[]), \
         patch("modules.ground_intel.fetch_youtube_geosearch", return_value=[]):
        res = client.get_ground_media_in_area("0.0,0.0", radius_km=25)
        assert res["status"] == "empty"
        assert res["total"] == 0
        assert len(res["points"]) == 0
        assert "No geotagged intel found within 25 km" in res["message"]
        assert "Wikimedia Commons" in res["sources_queried"]
        assert "USGS" in res["sources_queried"]

def test_authentic_item_schema_and_badge_contract():
    client = GroundIntelClient()
    fake_wiki_item = {
        "id": "wiki-123456",
        "platform": "Wikimedia Commons",
        "url": "https://commons.wikimedia.org/wiki/File:Test.jpg",
        "title": "Historical Tower in Test City",
        "author": "FieldPhotographer",
        "published_time": "2021-04-10T12:00:00Z",
        "lat": 35.6762,
        "lon": 139.6503,
        "geolocation_method": "GEOTAGGED",
        "fetched_at": "2026-10-08T07:00:00Z",
        "badge": "VERIFIED GEOLOCATION",
        "simulated": False,
        "media_type": "photo",
        "media_url": "https://upload.wikimedia.org/wikipedia/commons/test.jpg",
        "thumbnail_url": "https://upload.wikimedia.org/wikipedia/commons/test_thumb.jpg",
        "description": "Geotagged image",
        "source": "FieldPhotographer",
        "source_label": "Wikimedia",
    }
    with patch("modules.ground_intel.fetch_wikimedia_geosearch", return_value=[fake_wiki_item]), \
         patch("modules.ground_intel.fetch_usgs_earthquakes", return_value=[]), \
         patch("modules.ground_intel.fetch_nasa_eonet", return_value=[]), \
         patch("modules.ground_intel.fetch_gdacs_alerts", return_value=[]), \
         patch("modules.ground_intel.fetch_youtube_geosearch", return_value=[]):
        res = client.get_ground_media_in_area("35.6762,139.6503", radius_km=10)
        assert res["status"] == "ok"
        assert res["total"] == 1
        item = res["points"][0]
        assert item["id"] == "wiki-123456"
        assert item["geolocation_method"] == "GEOTAGGED"
        assert item["badge"] == "VERIFIED GEOLOCATION"
        assert item["simulated"] is False
        assert "lat" in item and "lon" in item
        assert "url" in item and item["url"].startswith("https://")
        assert "author" in item and item["author"] == "FieldPhotographer"
        assert "published_time" in item

def test_badge_never_verified_for_non_geotagged():
    client = GroundIntelClient()
    fake_place_matched = {
        "id": "gdacs-789",
        "platform": "GDACS",
        "url": "https://www.gdacs.org/alert",
        "title": "Regional Flood Advisory",
        "author": "GDACS Disaster Service",
        "published_time": "2026-10-08T00:00:00Z",
        "lat": 48.8566,
        "lon": 2.3522,
        "geolocation_method": "PLACE-MATCHED",
        "fetched_at": "2026-10-08T07:00:00Z",
        "badge": "PLACE-MATCHED",
        "simulated": False,
        "media_type": "event",
        "media_url": "https://www.gdacs.org/icon.png",
        "thumbnail_url": "https://www.gdacs.org/icon.png",
    }
    with patch("modules.ground_intel.fetch_wikimedia_geosearch", return_value=[]), \
         patch("modules.ground_intel.fetch_usgs_earthquakes", return_value=[]), \
         patch("modules.ground_intel.fetch_nasa_eonet", return_value=[]), \
         patch("modules.ground_intel.fetch_gdacs_alerts", return_value=[fake_place_matched]), \
         patch("modules.ground_intel.fetch_youtube_geosearch", return_value=[]):
        res = client.get_ground_media_in_area("48.8566,2.3522", radius_km=50)
        assert res["status"] == "ok"
        assert len(res["points"]) == 1
        assert res["points"][0]["badge"] != "VERIFIED GEOLOCATION"
        assert res["points"][0]["badge"] == "PLACE-MATCHED"
