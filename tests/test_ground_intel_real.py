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
        # Reachable "no results" path for Coimbatore with restored coordinates
        res = client.get_ground_media_in_area("11.0168,76.9558", radius_km=25)
        assert res["status"] == "empty"
        assert res["total"] == 0
        assert len(res["points"]) == 0
        assert "No geotagged intel found within 25 km" in res["message"]
        assert "Wikimedia Commons" in res["sources_queried"]
        assert "USGS" in res["sources_queried"]

        # Also verifies when queried by location name
        res_named = client.get_ground_media_in_area(location="Coimbatore", radius_km=25)
        assert res_named["status"] == "empty"
        assert res_named["total"] == 0
        assert len(res_named["points"]) == 0

def test_authentic_item_schema_and_badge_contract():
    client = GroundIntelClient()
    fake_wiki_item = {
        "id": "wiki-123456",
        "platform": "Wikimedia Commons",
        "url": "https://commons.wikimedia.org/wiki/File:Test.jpg",
        "title": "Historical Tower in Test City",
        "author": "FieldPhotographer",
        "published_time": "2021-04-10T12:00:00Z",
        "lat": 35.6850,
        "lon": 139.6600,
        "geolocation_method": "GEOTAGGED",
        "fetched_at": "2026-10-08T07:00:00Z",
        "badge": "UPLOADER GEOTAG",
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
        assert item["badge"] == "UPLOADER GEOTAG"
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
        "badge": "PLACE-MATCH",
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
        assert res["points"][0]["badge"] == "PLACE-MATCH"

def test_badge_recomputation_from_geolocation_method_when_wrong_badge_passed():
    """
    Asserts that passing items carrying a WRONG badge has the output badge strictly
    recomputed in code from geolocation_method.
    GEOTAGGED -> 'UPLOADER GEOTAG' (or 'CITY-LEVEL'). Everything else -> never verified.
    """
    client = GroundIntelClient()
    wrong_item_1 = {
        "id": "wrong-1",
        "platform": "TestNews",
        "title": "Item falsely claiming verified badge",
        "lat": 11.0168,
        "lon": 76.9558,
        "geolocation_method": "PLACE-MATCHED",
        "badge": "VERIFIED GEOLOCATION",  # WRONG badge: PLACE-MATCHED must never have verified badge
        "media_url": "https://example.com/1.jpg",
        "thumbnail_url": "https://example.com/1_t.jpg",
    }
    wrong_item_2 = {
        "id": "wrong-2",
        "platform": "TestGeo",
        "title": "Item with geotagged coords but wrong unverified badge",
        "lat": 11.0500,
        "lon": 76.9900,
        "geolocation_method": "GEOTAGGED",
        "badge": "UNVERIFIED RANDOM BADGE",  # WRONG badge: GEOTAGGED must be recomputed to UPLOADER GEOTAG
        "media_url": "https://example.com/2.jpg",
        "thumbnail_url": "https://example.com/2_t.jpg",
    }
    with patch("modules.ground_intel.fetch_wikimedia_geosearch", return_value=[wrong_item_1, wrong_item_2]), \
         patch("modules.ground_intel.fetch_usgs_earthquakes", return_value=[]), \
         patch("modules.ground_intel.fetch_nasa_eonet", return_value=[]), \
         patch("modules.ground_intel.fetch_gdacs_alerts", return_value=[]), \
         patch("modules.ground_intel.fetch_youtube_geosearch", return_value=[]):
        res = client.get_ground_media_in_area("11.0168,76.9558", radius_km=25)
        assert res["total"] == 2
        p1 = next(p for p in res["points"] if p["id"] == "wrong-1")
        p2 = next(p for p in res["points"] if p["id"] == "wrong-2")

        # p1 had wrong badge "VERIFIED GEOLOCATION" but geolocation_method="PLACE-MATCHED"
        assert p1["badge"] != "VERIFIED GEOLOCATION"
        assert p1["badge"] == "PLACE-MATCH"

        # p2 had wrong badge "UNVERIFIED RANDOM BADGE" but geolocation_method="GEOTAGGED"
        assert p2["badge"] == "UPLOADER GEOTAG"

