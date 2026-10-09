# tests/test_gods_eye_flight_enrichment.py
import unittest
import math
from modules.flight_intel import (
    great_circle_km,
    bearing_rad,
    cross_track_km,
    route_plausible,
    get_flight_intel_engine
)

class TestGodsEyeFlightEnrichment(unittest.TestCase):
    def setUp(self):
        self.engine = get_flight_intel_engine()

    def test_great_circle_distance(self):
        # DFW (32.8968, -97.0380) to HNL (21.3206, -157.9242) ~ 6080 km
        dist = great_circle_km(32.8968, -97.0380, 21.3206, -157.9242)
        self.assertAlmostEqual(dist, 6080.0, delta=100.0)

    def test_bearing_rad(self):
        # Northward bearing should be near 0 radians
        b = bearing_rad(0.0, 0.0, 10.0, 0.0)
        self.assertAlmostEqual(b, 0.0, places=4)

    def test_route_plausibility_on_track(self):
        origin = {'code': 'DFW', 'name': 'Dallas-Fort Worth', 'lat': 32.8968, 'lon': -97.0380}
        dest = {'code': 'HNL', 'name': 'Honolulu', 'lat': 21.3206, 'lon': -157.9242}
        
        # Point roughly halfway along DFW -> HNL (over California/Pacific, ~30°N, -125°W)
        on_track_lat, on_track_lon = 30.0, -125.0
        self.assertTrue(route_plausible(on_track_lat, on_track_lon, origin, dest, max_cross_track_km=500.0))

        # Point near origin (DFW)
        self.assertTrue(route_plausible(32.9, -97.0, origin, dest))

        # Completely wrong leg: Point in Europe (Paris: 48.85, 2.35)
        self.assertFalse(route_plausible(48.8566, 2.3522, origin, dest))

    def test_enrichment_cached_lookup(self):
        # AAL5 was previously fetched and cached in .cache/adsbdb.json
        enrichment = self.engine.get_flight_enrichment(
            callsign='AAL5',
            icao_hex='aadc16',
            current_lat=30.0,
            current_lon=-125.0
        )
        self.assertEqual(enrichment.get('callsign'), 'AAL5')
        self.assertEqual(enrichment.get('airline'), 'American Airlines')
        self.assertIsNotNone(enrichment.get('origin'))
        self.assertEqual(enrichment['origin']['code'], 'DFW')
        self.assertIsNotNone(enrichment.get('destination'))
        self.assertEqual(enrichment['destination']['code'], 'HNL')
        self.assertTrue(enrichment.get('route_plausible'))
        self.assertEqual(enrichment.get('route_summary'), 'DFW → HNL')

    def test_enrichment_empty_callsign_fallback(self):
        enrichment = self.engine.get_flight_enrichment(callsign='', icao_hex='ae01cd')
        self.assertEqual(enrichment.get('callsign'), '')
        self.assertIsNone(enrichment.get('airline'))
        self.assertIsNone(enrichment.get('route_summary'))

    def test_angular_interpolation_wrap(self):
        # Test lerpAngleDeg across North wrap-around (350 deg to 10 deg)
        def lerp_angle(a, b, t):
            diff = (b - a) % 360
            if diff < -180:
                diff += 360
            if diff > 180:
                diff -= 360
            return (a + diff * t + 360) % 360

        midpoint = lerp_angle(350, 10, 0.5)
        # Midpoint between 350° and 10° turning eastward across North should be 0° (or 360°)
        self.assertTrue(abs(midpoint - 0.0) < 0.001 or abs(midpoint - 360.0) < 0.001)

if __name__ == '__main__':
    unittest.main()
