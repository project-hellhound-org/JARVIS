# modules/flight_intel.py
import json
import math
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Optional

ADSB_MIL_URL = 'https://api.adsb.lol/v2/mil'
OPENSKY_URL = 'https://opensky-network.org/api/states/all'

# High-fidelity tactical contingency fixtures for offline or rate-limited environments
OFFLINE_MIL_FIXTURES = [
    {
        'hex': 'ae01cd',
        'flight': 'RCH842',
        'r': '02-1102',
        't': 'C17',
        'desc': 'BOEING C-17A Globemaster III',
        'lat': 35.3341,
        'lon': -97.5122,
        'alt_baro': 28000,
        'gs': 432.5,
        'track': 74.0,
        'squawk': '1200',
        'category': 'Military Transport',
        'source': 'offline_contingency'
    },
    {
        'hex': 'ae125c',
        'flight': 'IRON61',
        'r': '62-3512',
        't': 'KC135',
        'desc': 'BOEING KC-135R Stratotanker',
        'lat': 38.8122,
        'lon': -85.1243,
        'alt_baro': 24000,
        'gs': 390.2,
        'track': 268.0,
        'squawk': '3142',
        'category': 'Aerial Refueling',
        'source': 'offline_contingency'
    },
    {
        'hex': 'ae07d9',
        'flight': 'SENTRY01',
        'r': '79-0003',
        't': 'E3TF',
        'desc': 'BOEING E-3 Sentry (AWACS)',
        'lat': 36.2114,
        'lon': -76.4321,
        'alt_baro': 31000,
        'gs': 378.0,
        'track': 185.0,
        'squawk': '4410',
        'category': 'Airborne Early Warning',
        'source': 'offline_contingency'
    },
    {
        'hex': 'ae14a1',
        'flight': 'VIPER11',
        'r': '90-0801',
        't': 'F16',
        'desc': 'LOCKHEED F-16C Fighting Falcon',
        'lat': 32.8412,
        'lon': -117.1524,
        'alt_baro': 16500,
        'gs': 520.4,
        'track': 315.0,
        'squawk': '5211',
        'category': 'Fighter Interceptor',
        'source': 'offline_contingency'
    },
    {
        'hex': '43c6f1',
        'flight': 'RRR7215',
        'r': 'ZZ664',
        't': 'R135',
        'desc': 'BOEING RC-135W Rivet Joint',
        'lat': 54.2183,
        'lon': 18.5312,
        'alt_baro': 33000,
        'gs': 415.0,
        'track': 88.0,
        'squawk': '7001',
        'category': 'Electronic Reconnaissance',
        'feed_category': 'mil',
        'mil': 1,
        'source': 'offline_contingency'
    }
]

# Privacy ICAO Address (PIA) fixtures (Brown / Dark Amber tactical visualization)
OFFLINE_PIA_FIXTURES = [
    {
        'hex': 'a209b1',
        'flight': 'PRIV-44',
        'r': 'N128PA',
        't': 'GLF6',
        'desc': 'GULFSTREAM G650ER (PIA ENCRYPTED)',
        'lat': 41.2510,
        'lon': -73.6520,
        'alt_baro': 43000,
        'gs': 488.0,
        'track': 240.0,
        'squawk': '4102',
        'feed_category': 'pia',
        'pia': 1,
        'category': 'PIA Privacy Transponder',
        'source': 'offline_contingency'
    },
    {
        'hex': 'a5582f',
        'flight': 'STEALTH-9',
        'r': 'N552PX',
        't': 'GLEX',
        'desc': 'BOMBARDIER GLOBAL 7500 (PIA PRIVACY)',
        'lat': 34.0200,
        'lon': -118.4500,
        'alt_baro': 47000,
        'gs': 512.0,
        'track': 310.0,
        'squawk': '5120',
        'feed_category': 'pia',
        'pia': 1,
        'category': 'PIA Privacy Transponder',
        'source': 'offline_contingency'
    },
    {
        'hex': '40781a',
        'flight': 'GHOST-01',
        'r': 'G-OPRA',
        't': 'F900',
        'desc': 'DASSAULT FALCON 900LX (PIA ANONYMOUS)',
        'lat': 51.1537,
        'lon': -0.1821,
        'alt_baro': 39000,
        'gs': 465.0,
        'track': 125.0,
        'squawk': '6211',
        'feed_category': 'pia',
        'pia': 1,
        'category': 'PIA Privacy Transponder',
        'source': 'offline_contingency'
    }
]

# FAA Limiting Aircraft Data Displayed (LADD) fixtures (Deep Amber / Leather Brown visualization)
OFFLINE_LADD_FIXTURES = [
    {
        'hex': 'a9103c',
        'flight': 'LADD-82',
        'r': 'N882BL',
        't': 'C750',
        'desc': 'CESSNA CITATION X+ (FAA LADD BLOCKED)',
        'lat': 39.8561,
        'lon': -104.6737,
        'alt_baro': 45000,
        'gs': 525.0,
        'track': 175.0,
        'squawk': '3200',
        'feed_category': 'ladd',
        'ladd': 1,
        'category': 'FAA LADD Restricted',
        'source': 'offline_contingency'
    },
    {
        'hex': 'a7719d',
        'flight': 'EXECUTIVE-1',
        'r': 'N701EX',
        't': 'CL60',
        'desc': 'CHALLENGER 650 (LADD PROTECTED)',
        'lat': 25.7959,
        'lon': -80.2870,
        'alt_baro': 37000,
        'gs': 470.0,
        'track': 45.0,
        'squawk': '2214',
        'feed_category': 'ladd',
        'ladd': 1,
        'category': 'FAA LADD Restricted',
        'source': 'offline_contingency'
    }
]

# Emergency Squawk fixtures (Pulsing Alert Red visualization)
OFFLINE_EMERGENCY_FIXTURES = [
    {
        'hex': '4ca218',
        'flight': 'MAYDAY77',
        'r': 'EI-EMG',
        't': 'B738',
        'desc': 'BOEING 737-800 [GENERAL EMERGENCY SQUAWK 7700]',
        'lat': 53.4264,
        'lon': -6.2499,
        'alt_baro': 9800,
        'gs': 280.0,
        'track': 90.0,
        'squawk': '7700',
        'feed_category': 'emergency',
        'category': 'EMERGENCY SQUAWK 7700',
        'source': 'offline_contingency'
    }
]

# Commercial Airliner fixtures (Cyan / Sky Blue visualization)
OFFLINE_COMMERCIAL_FIXTURES = [
    {
        'hex': '80041a',
        'flight': 'AIC101',
        'r': 'VT-EXG',
        't': 'B77W',
        'desc': 'AIR INDIA BOEING 777-300ER',
        'lat': 28.5562,
        'lon': 77.1000,
        'alt_baro': 36000,
        'gs': 490.0,
        'track': 280.0,
        'squawk': '1410',
        'feed_category': 'commercial',
        'category': 'Commercial Air Transport',
        'source': 'offline_contingency'
    },
    {
        'hex': 'a194bc',
        'flight': 'UAL882',
        'r': 'N27901',
        't': 'B789',
        'desc': 'UNITED AIRLINES BOEING 787-9 DREAMLINER',
        'lat': 37.6188,
        'lon': -122.3750,
        'alt_baro': 38000,
        'gs': 505.0,
        'track': 250.0,
        'squawk': '4231',
        'feed_category': 'commercial',
        'category': 'Commercial Air Transport',
        'source': 'offline_contingency'
    }
]

OFFLINE_ALL_FIXTURES = (
    OFFLINE_MIL_FIXTURES +
    OFFLINE_PIA_FIXTURES +
    OFFLINE_LADD_FIXTURES +
    OFFLINE_EMERGENCY_FIXTURES +
    OFFLINE_COMMERCIAL_FIXTURES
)


# -------------------------------------------------------------------------
# God's Eye Flight Route Plausibility & Geometry (Bilawal Sidhu / routePlausible.js)
# -------------------------------------------------------------------------

D2R = math.pi / 180.0
R_KM = 6371.0


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine great-circle distance in kilometers."""
    p1 = lat1 * D2R
    p2 = lat2 * D2R
    dp = (lat2 - lat1) * D2R
    dl = (lon2 - lon1) * D2R
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * (math.sin(dl / 2.0) ** 2)
    return R_KM * 2.0 * math.atan2(math.sqrt(max(0.0, a)), math.sqrt(max(0.0, 1.0 - a)))


def bearing_rad(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = lat1 * D2R
    p2 = lat2 * D2R
    dl = (lon2 - lon1) * D2R
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.atan2(y, x)


def cross_track_km(lat: float, lon: float, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Signed cross-track distance (km) of point from the great circle p1 -> p2."""
    d13 = great_circle_km(lat1, lon1, lat, lon) / R_KM
    b13 = bearing_rad(lat1, lon1, lat, lon)
    b12 = bearing_rad(lat1, lon1, lat2, lon2)
    return math.asin(max(-1.0, min(1.0, math.sin(d13) * math.sin(b13 - b12)))) * R_KM


def route_plausible(
    lat: Optional[float],
    lon: Optional[float],
    origin: Optional[Dict[str, Any]],
    destination: Optional[Dict[str, Any]],
    max_cross_track_km: float = 350.0
) -> bool:
    """
    Validates whether an adsbdb scheduled route matches the aircraft's actual position.
    Rejects wrong-leg routes (e.g. return flights from previous cycle).
    """
    if lat is None or lon is None or not origin or not destination:
        return True
    o_lat = origin.get("lat")
    o_lon = origin.get("lon")
    d_lat = destination.get("lat")
    d_lon = destination.get("lon")
    if o_lat is None or o_lon is None or d_lat is None or d_lon is None:
        return True

    total_corridor = great_circle_km(o_lat, o_lon, d_lat, d_lon)
    if total_corridor < 50.0:
        return True

    dist_origin = great_circle_km(lat, lon, o_lat, o_lon)
    dist_dest = great_circle_km(lat, lon, d_lat, d_lon)

    # Near either terminal
    if dist_origin < 150.0 or dist_dest < 150.0:
        return True

    # Far off corridor sum
    if (dist_origin + dist_dest) > (total_corridor * 1.6 + 250.0):
        return False

    xt = abs(cross_track_km(lat, lon, o_lat, o_lon, d_lat, d_lon))
    return xt <= max_cross_track_km


class FlightIntelEngine:
    """
    Real-time Airspace & Military Radar Intelligence Engine.
    Queries adsb.lol / OpenSky feeds for live military tracking,
    extracts flight trajectories, and feeds real-time telemetry into J.A.R.V.I.S. HUD.
    """
    def __init__(self):
        self.cache_ttl = 15
        self._last_fetch_time = 0
        self._cached_flights: List[Dict[str, Any]] = []
        self._init_enrichment_cache()

    def get_military_aircraft(self, limit: int = 12) -> List[Dict[str, Any]]:
        """
        Fetch active worldwide military flights from adsb.lol/v2/mil.
        Falls back to OpenSky or offline contingency fixtures.
        """
        now = time.time()
        if self._cached_flights and (now - self._last_fetch_time) < self.cache_ttl:
            return self._cached_flights[:limit]

        flights = []
        # Attempt 1: adsb.lol military API
        try:
            req = urllib.request.Request(
                ADSB_MIL_URL,
                headers={
                    'User-Agent': 'JARVIS-AirspaceMonitor/2.0 (Tactical Recon HUD)',
                    'Accept': 'application/json'
                }
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                ac_list = data.get('ac', [])
                for ac in ac_list:
                    flight_id = (ac.get('flight') or ac.get('r') or ac.get('hex') or '').strip()
                    if not flight_id:
                        continue
                    flights.append({
                        'hex': ac.get('hex', '').strip(),
                        'flight': flight_id,
                        'r': ac.get('r', '').strip(),
                        't': ac.get('t', 'MIL').strip(),
                        'desc': ac.get('desc', ac.get('t', 'Military Aircraft')).strip(),
                        'lat': ac.get('lat', 0.0),
                        'lon': ac.get('lon', 0.0),
                        'alt_baro': ac.get('alt_baro', ac.get('alt_geom', 0)),
                        'gs': ac.get('gs', 0.0),
                        'track': ac.get('track', 0.0),
                        'squawk': ac.get('squawk', 'N/A'),
                        'category': 'Military Airspace',
                        'source': 'adsb.lol'
                    })
        except Exception as e:
            # Silently catch network or sandbox restrictions
            pass

        # Attempt 1.5: OSIRIS Live Military Flights
        if not flights:
            try:
                from modules.osiris_intel import get_osiris_client
                osiris_flights = get_osiris_client().get_flights(military_only=True)
                for mf in osiris_flights.get("military", []):
                    f_id = (mf.get("callsign") or mf.get("registration") or mf.get("icao24") or "MIL-FLIGHT").strip()
                    flights.append({
                        'hex': mf.get('icao24', '').strip(),
                        'flight': f_id,
                        'r': mf.get('registration', '').strip(),
                        't': mf.get('model', 'MIL').strip(),
                        'desc': mf.get('model', 'Military Combat Aircraft').strip(),
                        'lat': mf.get('lat', 0.0),
                        'lon': mf.get('lng', 0.0),
                        'alt_baro': mf.get('alt', 0),
                        'gs': mf.get('speed_knots', 0.0),
                        'track': mf.get('heading', 0.0),
                        'squawk': mf.get('squawk', 'N/A'),
                        'category': 'Military Airspace',
                        'source': 'osiris'
                    })
            except Exception as e:
                pass

        # Attempt 2: If no flights returned, use contingency fixtures
        if not flights:
            flights = list(OFFLINE_MIL_FIXTURES)

        self._cached_flights = flights
        self._last_fetch_time = now
        return flights[:limit]

    def get_flight_trace(self, icao_hex: str) -> Optional[Dict[str, Any]]:
        """
        Fetch 24h trajectory trace points for a given ICAO hex code from adsb.lol.
        """
        hex_clean = icao_hex.strip().lower()
        if len(hex_clean) < 2:
            return None
        url = f'https://adsb.lol/data/traces/{hex_clean[-2:]}/trace_full_{hex_clean}.json'
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'JARVIS-AirspaceMonitor/2.0'}
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception:
            return None

    def format_tactical_debrief(self, flights: List[Dict[str, Any]]) -> str:
        """
        Generate J.A.R.V.I.S. spoken briefing on active military targets.
        """
        if not flights:
            return 'Airspace radar reports no military transponder signatures in range, Sir.'

        top = flights[0]
        desc = top.get('desc') or top.get('t') or 'tactical aircraft'
        callsign = top.get('flight', 'Unknown')
        alt_raw = top.get('alt_baro', 0)
        speed = top.get('gs', 0)
        if str(alt_raw).lower() == 'ground':
            alt_phrase = 'on the ground'
        else:
            try:
                alt_phrase = f'at {int(float(alt_raw)):,} feet'
            except (ValueError, TypeError):
                alt_phrase = f'at {alt_raw} feet'

        lines = [
            f'Locked onto {len(flights)} military radar signatures.',
            f'Lead track is {callsign} ({desc}) {alt_phrase}, tearing through at {speed} knots.'
        ]
        if len(flights) > 1:
            second = flights[1]
            sec_alt = second.get('alt_baro', 0)
            sec_alt_phrase = 'on the ground' if str(sec_alt).lower() == 'ground' else f'at {sec_alt} ft'
            lines.append(f'Secondary target {second.get("flight", "VIPER")} ({second.get("t", "F16")}) {sec_alt_phrase}.')
        return ' '.join(lines)

    # -------------------------------------------------------------------------
    # God's Eye adsbdb.com Enrichment & Caching (Bilawal Sidhu / enrichment.js)
    # -------------------------------------------------------------------------
    def _init_enrichment_cache(self):
        self._cache_dir = Path(__file__).resolve().parent.parent / '.cache'
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._adsbdb_cache_file = self._cache_dir / 'adsbdb.json'
        self._enrich_routes: Dict[str, Any] = {}
        self._enrich_aircraft: Dict[str, Any] = {}
        self._enrich_ttl = 24 * 3600  # 24 hours

        if self._adsbdb_cache_file.exists():
            try:
                with open(self._adsbdb_cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._enrich_routes = data.get('routes', {})
                    self._enrich_aircraft = data.get('aircraft', {})
            except Exception:
                pass

    def _persist_enrichment_cache(self):
        try:
            with open(self._adsbdb_cache_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'routes': self._enrich_routes,
                    'aircraft': self._enrich_aircraft
                }, f)
        except Exception:
            pass

    def get_flight_route(self, callsign: str) -> Optional[Dict[str, Any]]:
        callsign_clean = (callsign or '').strip().upper()
        if not callsign_clean or len(callsign_clean) < 3:
            return None

        now = time.time()
        cached = self._enrich_routes.get(callsign_clean)
        if cached and (now - cached.get('at', 0)) < self._enrich_ttl:
            return cached.get('data')

        url = f'https://api.adsbdb.com/v0/callsign/{urllib.parse.quote(callsign_clean)}'
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'JARVIS-Tactical/2.0 (Gods-Eye-View-Parity)'}
            )
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                fr = data.get('response', {}).get('flightroute')
                if not fr:
                    self._enrich_routes[callsign_clean] = {'at': now, 'data': None}
                    self._persist_enrichment_cache()
                    return None

                origin = fr.get('origin', {})
                dest = fr.get('destination', {})
                parsed = {
                    'airline': (fr.get('airline') or {}).get('name'),
                    'airline_icao': (fr.get('airline') or {}).get('icao'),
                    'origin': {
                        'code': origin.get('iata_code') or origin.get('icao_code') or '',
                        'name': origin.get('municipality') or origin.get('name') or '',
                        'lat': origin.get('latitude'),
                        'lon': origin.get('longitude')
                    },
                    'destination': {
                        'code': dest.get('iata_code') or dest.get('icao_code') or '',
                        'name': dest.get('municipality') or dest.get('name') or '',
                        'lat': dest.get('latitude'),
                        'lon': dest.get('longitude')
                    }
                }
                self._enrich_routes[callsign_clean] = {'at': now, 'data': parsed}
                self._persist_enrichment_cache()
                return parsed
        except urllib.error.HTTPError as he:
            if he.code == 404:
                # Negative cache 404 to protect public API from repeated queries on unknown callsigns
                self._enrich_routes[callsign_clean] = {'at': now, 'data': None}
                self._persist_enrichment_cache()
            return None
        except Exception:
            return None

    def get_aircraft_meta(self, icao_hex: str) -> Optional[Dict[str, Any]]:
        hex_clean = (icao_hex or '').strip().lower()
        if not hex_clean or len(hex_clean) != 6:
            return None

        now = time.time()
        cached = self._enrich_aircraft.get(hex_clean)
        if cached and (now - cached.get('at', 0)) < self._enrich_ttl:
            return cached.get('data')

        url = f'https://api.adsbdb.com/v0/aircraft/{urllib.parse.quote(hex_clean)}'
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'JARVIS-Tactical/2.0 (Gods-Eye-View-Parity)'}
            )
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                ac = data.get('response', {}).get('aircraft')
                if not ac:
                    self._enrich_aircraft[hex_clean] = {'at': now, 'data': None}
                    self._persist_enrichment_cache()
                    return None

                manufacturer = ac.get('manufacturer') or ''
                ac_type = ac.get('type') or ''
                type_name = f"{manufacturer} {ac_type}".strip() if manufacturer and ac_type else ac_type
                parsed = {
                    'type_code': ac.get('icao_type'),
                    'type_name': type_name or None,
                    'registration': ac.get('registration') or None,
                    'registered_owner': ac.get('registered_owner') or None
                }
                self._enrich_aircraft[hex_clean] = {'at': now, 'data': parsed}
                self._persist_enrichment_cache()
                return parsed
        except urllib.error.HTTPError as he:
            if he.code == 404:
                self._enrich_aircraft[hex_clean] = {'at': now, 'data': None}
                self._persist_enrichment_cache()
            return None
        except Exception:
            return None

    def get_flight_enrichment(
        self,
        callsign: str,
        icao_hex: str = '',
        current_lat: Optional[float] = None,
        current_lon: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Enrich flight with operator airline name, aircraft type description, and verified origin -> destination route.
        """
        route = self.get_flight_route(callsign)
        meta = self.get_aircraft_meta(icao_hex) if icao_hex else None

        is_plausible = True
        if route and current_lat is not None and current_lon is not None:
            is_plausible = route_plausible(
                current_lat, current_lon,
                route.get('origin'), route.get('destination')
            )

        origin_code = (route.get('origin') or {}).get('code', '') if route else ''
        dest_code = (route.get('destination') or {}).get('code', '') if route else ''
        route_str = f"{origin_code} → {dest_code}" if (origin_code and dest_code) else None

        return {
            'callsign': (callsign or '').strip().upper(),
            'hex': (icao_hex or '').strip().lower() if icao_hex else '',
            'airline': (route.get('airline') if route else None),
            'origin': (route.get('origin') if route else None),
            'destination': (route.get('destination') if route else None),
            'route_plausible': is_plausible,
            'route_summary': route_str if is_plausible else None,
            'type_code': (meta.get('type_code') if meta else None),
            'type_name': (meta.get('type_name') if meta else None),
            'registration': (meta.get('registration') if meta else None),
            'owner': (meta.get('registered_owner') if meta else None),
        }


_DEFAULT_ENGINE = None

def get_flight_intel_engine() -> FlightIntelEngine:
    global _DEFAULT_ENGINE
    if _DEFAULT_ENGINE is None:
        _DEFAULT_ENGINE = FlightIntelEngine()
    return _DEFAULT_ENGINE

