import unittest
from pathlib import Path
from modules.cyber_news_service import get_latest_cyber_news
from modules.ground_intel import get_ground_intel_client

class TestGodsEyeRound2(unittest.TestCase):
    def test_cyber_news_service_payload(self):
        items = get_latest_cyber_news()
        self.assertIsInstance(items, list)
        self.assertGreaterEqual(len(items), 1)
        for item in items:
            self.assertIn("id", item)
            self.assertIn("title", item)
            self.assertIn("source", item)
            self.assertIn("summary", item)
            self.assertIn("date", item)
            self.assertIn("severity", item)

    def test_ground_intel_real_data_only(self):
        client = get_ground_intel_client()
        intel = client.get_ground_media_in_area(11.0168, 76.9558, location="Coimbatore")
        points = intel.get("media_points", [])
        if intel.get("status") == "OFFLINE":
            self.assertEqual(len(points), 0)
            self.assertEqual(intel.get("message"), "OFFLINE - no live data")
            return
        self.assertGreaterEqual(len(points), 1)
        for item in points:
            self.assertIn("platform", item)
            self.assertIn("url", item)
            self.assertIn("title", item)
            self.assertIn("author", item)
            self.assertIn("published_time", item)
            self.assertIn("geolocation_method", item)
            self.assertIn("fetched_at", item)
            self.assertIn("badge", item)
            self.assertFalse(item.get("simulated", False))
            if item["geolocation_method"] == "GEOTAGGED":
                self.assertIn(item["badge"], ("UPLOADER GEOTAG", "CITY-LEVEL"))

    def test_app_html_osint_radio_and_cyber_news(self):
        html = Path("frontend/app.html").read_text(encoding="utf-8")
        # Categories
        self.assertIn("'atc'", html)
        self.assertIn("'scanner'", html)
        self.assertIn("'marine'", html)
        self.assertIn("'news'", html)
        self.assertIn("'cyber'", html)
        # Terms present on all stations
        self.assertIn("terms:", html)
        self.assertIn("NOAA / NWS:", html)
        self.assertIn("SomaFM:", html)
        self.assertIn("BBC:", html)
        # Live playback status badge
        self.assertIn("radio-stream-badge", html)
        self.assertIn("BUFFERING", html)
        # Cyber news panel
        self.assertIn("cyber-news-panel", html)
        self.assertIn("fetchCyberNews", html)

    def test_app_html_ground_media_floating_cards(self):
        html = Path("frontend/app.html").read_text(encoding="utf-8")
        self.assertIn("ground-media-auto-floating-cards", html)
        self.assertIn("updateAutoFloatingGroundMediaCards", html)
        self.assertIn("EllipsoidalOccluder", html)
        self.assertIn("SIMULATED", html)

    def test_app_html_satellite_billboard_sprites(self):
        html = Path("frontend/app.html").read_text(encoding="utf-8")
        # Batched BillboardCollection
        self.assertIn("new Cesium.BillboardCollection(", html)
        self.assertIn("_satelliteBillboards", html)
        # Procedural sprite generator
        self.assertIn("getSatelliteSpriteCanvas", html)
        # Distance display condition for labels > 3000 km altitude
        self.assertIn("DistanceDisplayCondition(0, 3000000)", html)

if __name__ == '__main__':
    unittest.main()
