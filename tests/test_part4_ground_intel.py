# tests/test_part4_ground_intel.py
import json
import urllib.request
from unittest.mock import patch, MagicMock
from modules.ground_intel import (
    calculate_bearing,
    extract_year,
    strip_html_tags,
    get_wikimedia_user_agent,
    fetch_wikimedia_geosearch,
    get_ground_intel_client,
    GroundIntelClient,
    USGS_TILE_REGEX,
    ISS_PHOTO_REGEX,
    CAMERA_NAME_REGEX,
)

def test_calculate_bearing():
    # Due North: lat increases, lon stays constant
    b_north = calculate_bearing(10.0, 70.0, 11.0, 70.0)
    assert b_north.startswith("N (")

    # Due East: lat constant, lon increases
    b_east = calculate_bearing(10.0, 70.0, 10.0, 71.0)
    assert b_east.startswith("E (")

    # Due South: lat decreases, lon constant
    b_south = calculate_bearing(10.0, 70.0, 9.0, 70.0)
    assert b_south.startswith("S (")

    # Due West: lat constant, lon decreases
    b_west = calculate_bearing(10.0, 70.0, 10.0, 69.0)
    assert b_west.startswith("W (")

    # North-East
    b_ne = calculate_bearing(10.0, 70.0, 11.0, 71.0)
    assert "NE" in b_ne

def test_extract_year():
    assert extract_year("2021-05-14T10:30:00Z") == 2021
    assert extract_year("1998:10:25 12:00:00") == 1998
    assert extract_year("Taken in 1945 during the conference") == 1945
    assert extract_year(None) is None
    assert extract_year("invalid-date") is None

def test_strip_html_tags():
    raw = '<a href="https://example.com">John Doe</a> &amp; Jane &quot;Smith&quot;'
    cleaned = strip_html_tags(raw)
    assert cleaned == 'John Doe & Jane "Smith"'
    assert strip_html_tags(None) == ""

def test_user_agent_format():
    ua = get_wikimedia_user_agent()
    assert ua.startswith("JARVIS-OSINT/1.0 (")
    assert ua.endswith(")")
    assert "@" in ua

def test_filter_regexes():
    # USGS tile
    assert USGS_TILE_REGEX.search("M_3309859_nw_14_060_20220703.tif")
    assert USGS_TILE_REGEX.search("M 3309859 nw 14 060 20220703")

    # ISS astronaut photos
    assert ISS_PHOTO_REGEX.search("ISS067-E-123456.jpg")
    assert ISS_PHOTO_REGEX.search("ISS042-E-050123")

    # Camera filenames
    assert CAMERA_NAME_REGEX.match("IMG_4920")
    assert CAMERA_NAME_REGEX.match("DSC_0032")
    assert CAMERA_NAME_REGEX.match("DJI_0102")
    assert CAMERA_NAME_REGEX.match("20210504_120304")
    assert CAMERA_NAME_REGEX.match("12345678901")

def test_wikimedia_geosearch_query_parameters_and_metadata_parsing():
    sample_api_response = {
        "query": {
            "pages": {
                "101": {
                    "pageid": 101,
                    "title": "File:Historic_Clock_Tower_Square.jpg",
                    "coordinates": [{"lat": 11.0200, "lon": 76.9600}],
                    "categories": [
                        {"title": "Category:Clock towers in Tamil Nadu"},
                        {"title": "Category:Heritage buildings"}
                    ],
                    "imageinfo": [{
                        "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Historic_Clock_Tower_Square.jpg",
                        "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Historic_Clock_Tower_Square.jpg/800px-Historic_Clock_Tower_Square.jpg",
                        "user": "ArchiveUploader",
                        "timestamp": "2020-03-12T08:00:00Z",
                        "extmetadata": {
                            "ImageDescription": {"value": "<p>A 19th century clock tower standing in the town center.</p>"},
                            "DateTimeOriginal": {"value": "2019-11-20 15:30:00"},
                            "Artist": {"value": "<b>Marcus Vance</b>"},
                            "LicenseShortName": {"value": "CC BY-SA 4.0"}
                        }
                    }]
                },
                "102": {
                    # Camera filename that should be renamed from first sentence of ImageDescription
                    "pageid": 102,
                    "title": "File:IMG_20210515_142010.jpg",
                    "coordinates": [{"lat": 11.0250, "lon": 76.9650}],
                    "categories": [{"title": "Category:Botanical gardens"}],
                    "imageinfo": [{
                        "url": "https://upload.wikimedia.org/wikipedia/commons/c/c2/IMG_20210515_142010.jpg",
                        "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/IMG_20210515_142010.jpg/800px-IMG_20210515_142010.jpg",
                        "user": "NatureFan",
                        "timestamp": "2021-05-16T10:00:00Z",
                        "extmetadata": {
                            "ImageDescription": {"value": "Rose garden blossoming in springtime. Captured on a sunny morning."},
                            "DateTimeOriginal": {"value": "2021-05-15 14:20:10"},
                            "Artist": {"value": "NatureFan"},
                            "LicenseShortName": {"value": "CC0"}
                        }
                    }]
                },
                "103": {
                    # Should be dropped: TIFF file
                    "pageid": 103,
                    "title": "File:M_3309859_nw_14_060_20220703.tif",
                    "coordinates": [{"lat": 11.0200, "lon": 76.9600}],
                    "imageinfo": [{"url": "https://upload.wikimedia.org/wikipedia/commons/m.tif"}]
                },
                "104": {
                    # Should be dropped: SVG file
                    "pageid": 104,
                    "title": "File:Coat_of_arms_of_the_city.svg",
                    "coordinates": [{"lat": 11.0200, "lon": 76.9600}],
                    "imageinfo": [{"url": "https://upload.wikimedia.org/wikipedia/commons/c.svg"}]
                },
                "105": {
                    # Should be dropped: ISS Astronaut photo
                    "pageid": 105,
                    "title": "File:ISS067-E-123456.jpg",
                    "coordinates": [{"lat": 11.0200, "lon": 76.9600}],
                    "imageinfo": [{"url": "https://upload.wikimedia.org/wikipedia/commons/iss.jpg"}]
                }
            }
        }
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(sample_api_response).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        items, status = fetch_wikimedia_geosearch(11.0168, 76.9558, radius_km=10.0, limit=20)
        assert status == "OK"

        # Check requested URL has iiurlwidth=800 and extmetadata
        call_args = mock_urlopen.call_args_list[0]
        req = call_args[0][0]
        url = req.full_url
        assert "iiurlwidth=800" in url
        assert "extmetadata" in url
        assert "categories" in url

        # 103 (tif), 104 (svg), 105 (ISS) must all be dropped!
        ids = [it["id"] for it in items]
        assert "wiki-103" not in ids
        assert "wiki-104" not in ids
        assert "wiki-105" not in ids

        # Item 101 checks
        item101 = next(it for it in items if it["id"] == "wiki-101")
        assert item101["title"] == "Historic Clock Tower Square"
        assert item101["thumbnail_url"].endswith("/800px-Historic_Clock_Tower_Square.jpg")
        assert item101["author"] == "Marcus Vance"
        assert item101["license"] == "CC BY-SA 4.0"
        assert item101["date_taken"] == "2019-11-20 15:30:00"
        assert item101["year"] == 2019
        assert "bearing" in item101 and "(" in item101["bearing"]
        assert "Clock towers in Tamil Nadu" in item101["categories"]
        assert item101["description"] == "A 19th century clock tower standing in the town center."
        # Never generate fake filler text!
        assert "Geotagged Wikimedia field photograph" not in item101["description"]

        # Item 102 checks: camera filename rewritten to first sentence of ImageDescription
        item102 = next(it for it in items if it["id"] == "wiki-102")
        assert item102["title"] == "Rose garden blossoming in springtime."
        assert item102["license"] == "CC0"
        assert item102["year"] == 2021

def test_ground_intel_client_caps_at_40_items():
    client = GroundIntelClient()
    fake_items = []
    for i in range(60):
        fake_items.append({
            "id": f"wiki-{1000 + i}",
            "platform": "Wikimedia Commons",
            "url": f"https://commons.wikimedia.org/wiki/File:Item_{i}.jpg",
            "title": f"Contact Landmark {i}",
            "author": f"User_{i}",
            "license": "CC BY-SA 4.0",
            "lat": 12.0 + (i * 0.001),
            "lon": 77.0 + (i * 0.001),
            "distance_km": float(i * 0.2),
            "geolocation_method": "GEOTAGGED",
            "badge": "UPLOADER GEOTAG",
            "media_type": "photo",
            "thumbnail_url": f"https://upload.wikimedia.org/thumb/item_{i}.jpg",
            "media_url": f"https://upload.wikimedia.org/item_{i}.jpg",
            "description": f"Real photograph {i}"
        })

    with patch("modules.ground_intel.fetch_wikimedia_geosearch", return_value=fake_items), \
         patch("modules.ground_intel.fetch_usgs_earthquakes", return_value=[]), \
         patch("modules.ground_intel.fetch_nasa_eonet", return_value=[]), \
         patch("modules.ground_intel.fetch_gdacs_alerts", return_value=[]), \
         patch("modules.ground_intel.fetch_youtube_geosearch", return_value=[]):
        res = client.get_ground_media_in_area("12.0,77.0", radius_km=50, limit=50)
        assert res["status"] == "ok"
        # Must be capped at 40 nearest
        assert len(res["points"]) == 40
        assert res["total"] == 40
        for p in res["points"]:
            assert "bearing" in p

def test_offline_fallback_seed_structure():
    with patch("urllib.request.urlopen", side_effect=Exception("No internet")):
        items, status = fetch_wikimedia_geosearch(11.02, 76.96, radius_km=25.0)
        assert items == []
        assert status == "OFFLINE"
