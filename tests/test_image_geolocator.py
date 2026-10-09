import io
import json
import unittest
from unittest.mock import patch
from pathlib import Path
from PIL import Image

from modules.image_geolocator import (
    ImageGeolocator,
    extract_exif_metadata,
    downscale_image_for_model
)


class TestImageGeolocator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = Path("/tmp/jarvis_test_geolocator")
        cls.test_dir.mkdir(parents=True, exist_ok=True)

        cls.gps_img_path = cls.test_dir / "test_with_gps.jpg"
        img = Image.new("RGB", (200, 200), color=(73, 109, 137))
        exif = img.getexif()

        exif[271] = "JARVIS Optical Sensor"
        exif[272] = "Mark VII"
        exif[306] = "2026:10:09 12:00:00"

        gps_ifd = exif.get_ifd(0x8825)
        gps_ifd[1] = "N"
        gps_ifd[2] = (11.0, 1.0, 0.48)
        gps_ifd[3] = "E"
        gps_ifd[4] = (76.0, 57.0, 20.88)
        img.save(cls.gps_img_path, "JPEG", exif=exif)

        cls.no_gps_img_path = cls.test_dir / "test_no_gps.jpg"
        img_plain = Image.new("RGB", (300, 300), color=(20, 40, 60))
        img_plain.save(cls.no_gps_img_path, "JPEG")

    def test_exif_gps_extraction(self):
        metadata = extract_exif_metadata(str(self.gps_img_path))
        self.assertTrue(metadata["has_gps"])
        self.assertIsNotNone(metadata["gps"])
        self.assertAlmostEqual(metadata["gps"]["lat"], 11.0168, places=3)
        self.assertAlmostEqual(metadata["gps"]["lon"], 76.9558, places=3)
        self.assertEqual(metadata["camera_make"], "JARVIS Optical Sensor")
        self.assertEqual(metadata["camera_model"], "Mark VII")
        self.assertEqual(metadata["label"], "from file metadata (high confidence, but verify)")

    def test_downscale_image(self):
        large_path = self.test_dir / "test_large.jpg"
        large_img = Image.new("RGB", (2048, 1536), color=(255, 0, 0))
        large_img.save(large_path, "JPEG")

        raw_bytes, b64 = downscale_image_for_model(str(large_path), max_dimension=1024)
        self.assertIsInstance(raw_bytes, bytes)
        self.assertIsInstance(b64, str)

        with Image.open(io.BytesIO(raw_bytes)) as downscaled:
            w, h = downscaled.size
            self.assertLessEqual(w, 1024)
            self.assertLessEqual(h, 1024)

    def test_json_validation_and_cleaning(self):
        geolocator = ImageGeolocator()

        markdown_json = "```json\n{\"architecture\": {\"observation\": \"European Gothic\", \"confidence\": 0.9}}\n```"
        cleaned = geolocator._clean_json_output(markdown_json)
        self.assertIsInstance(cleaned, dict)
        self.assertEqual(cleaned["architecture"]["confidence"], 0.9)

        array_json = "```json\n[{\"rank\": 1, \"country\": \"France\", \"confidence\": 0.85}]\n```"
        cleaned_arr = geolocator._clean_json_output(array_json)
        self.assertIsInstance(cleaned_arr, list)
        self.assertEqual(cleaned_arr[0]["country"], "France")

    def test_no_vision_model_fallback_zero_guessing(self):
        geolocator = ImageGeolocator()
        with patch.object(geolocator, "_execute_vision_prompt", return_value=(None, None)):
            res = geolocator.analyze(str(self.no_gps_img_path))
            self.assertIsNone(res["provider_used"])
            self.assertEqual(res["warning"], "No vision model available - add a Gemini key or enable Ollama.")
            self.assertEqual(len(res["candidates"]), 0)
            self.assertIn("no vision model is currently available", res["speech_summary"].lower())

    def test_exif_candidate_preserved_when_no_model(self):
        geolocator = ImageGeolocator()
        with patch.object(geolocator, "_execute_vision_prompt", return_value=(None, None)), \
             patch.object(geolocator.geocoder, "reverse_geocode", return_value={"display_name": "Coimbatore, Tamil Nadu", "address": {"city": "Coimbatore", "country": "India"}}):
            res = geolocator.analyze(str(self.gps_img_path))
            self.assertEqual(len(res["candidates"]), 1)
            cand = res["candidates"][0]
            self.assertEqual(cand["rank"], 0)
            self.assertEqual(cand["hypothesis_status"], "from file metadata (high confidence, but verify)")
            self.assertAlmostEqual(cand["latitude"], 11.0168, places=3)
            self.assertIn("GPS coordinates were extracted directly from the file metadata", res["speech_summary"])

    def test_mocked_model_vision_analysis(self):
        geolocator = ImageGeolocator()

        mock_obs = {
            "architecture": {"observation": "Haussmannian stone facades", "confidence": 0.88},
            "signage_text": {"observation": "Boulangerie", "languages": ["French"], "confidence": 0.92},
            "driving_side": {"observation": "right", "confidence": 0.80}
        }
        mock_candidates = [
            {
                "rank": 1,
                "country": "France",
                "region": "Île-de-France",
                "place": "Paris",
                "latitude": 48.8566,
                "longitude": 2.3522,
                "confidence": 0.88,
                "clues": ["Haussmannian building design", "French signage"],
                "what_to_check_next": ["Verify street name sign typography"]
            }
        ]

        with patch.object(geolocator, "step1_extract_observations", return_value=(mock_obs, "gemini")), \
             patch.object(geolocator, "step2_rank_hypotheses", return_value=mock_candidates), \
             patch.object(geolocator, "get_nearby_wikimedia_photos", return_value=[]):
            res = geolocator.analyze(str(self.no_gps_img_path))
            self.assertEqual(res["provider_used"], "gemini")
            self.assertIsNone(res["warning"])
            self.assertEqual(len(res["candidates"]), 1)
            self.assertEqual(res["candidates"][0]["country"], "France")
            self.assertIn("Paris", res["speech_summary"])


if __name__ == "__main__":
    unittest.main()
