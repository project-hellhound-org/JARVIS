# modules/osiris_intel.py
"""
OSIRIS Intelligence Platform Engine for J.A.R.V.I.S.
Connects J.A.R.V.I.S. directly to the OSIRIS Global Intelligence Platform (https://osirisai.live).

Capabilities:
- Real-Time Aviation: Commercial, Military Flights, Private Jets & GPS Jamming Sectors
- 18,800+ Orbital Satellites with TLE Telemetry & Pass Predictions
- Active Warzones, Frontline Geometry & Live Conflict Incident Reporting
- Turn-by-Turn Valhalla/OSRM Street Navigation
- OSINT Cyber RECON (DNS, WHOIS, SSL Certs, IP/ASN, Shodan, CVEs, Sanctions)
- Real-Time Environmental & Space Weather Telemetry
"""

import os
import json
import math
import time
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("JarvisOsiris")

DEFAULT_OSIRIS_URL = "https://osirisai.live"
CACHE_DIR = Path.home() / ".jarvis" / "osiris_cache"

# TTL in seconds for specific endpoints
DEFAULT_TTLS = {
    "stats": 10,
    "flights": 15,
    "satellites": 30,
    "conflicts": 60,
    "directions": 300,
    "osint": 300,
    "maritime": 60,
    "news": 120,
    "space_weather": 180,
    "infrastructure": 600,
}


def parse_bounds(bounds: Any) -> Optional[Tuple[float, float, float, float]]:
    """
    Normalizes bounding box representations to (min_lat, min_lon, max_lat, max_lon).
    Supports:
      - dict: {'min_lat': ..., 'min_lon': ..., 'max_lat': ..., 'max_lon': ...}
      - dict: {'south': ..., 'west': ..., 'north': ..., 'east': ...}
      - list/tuple: [south, west, north, east] or [min_lat, min_lon, max_lat, max_lon]
    """
    if not bounds:
        return None
    try:
        if isinstance(bounds, dict):
            if "min_lat" in bounds and "max_lat" in bounds and "min_lon" in bounds and "max_lon" in bounds:
                return (
                    float(bounds["min_lat"]),
                    float(bounds["min_lon"]),
                    float(bounds["max_lat"]),
                    float(bounds["max_lon"]),
                )
            if "south" in bounds and "north" in bounds and "west" in bounds and "east" in bounds:
                return (
                    float(bounds["south"]),
                    float(bounds["west"]),
                    float(bounds["north"]),
                    float(bounds["east"]),
                )
        elif isinstance(bounds, (list, tuple)) and len(bounds) >= 4:
            return (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
    except (ValueError, TypeError):
        pass
    return None


def is_point_in_bounds(lat: Optional[float], lon: Optional[float], bounds: Tuple[float, float, float, float]) -> bool:
    """Check if (lat, lon) falls inside (min_lat, min_lon, max_lat, max_lon)."""
    if lat is None or lon is None:
        return False
    min_lat, min_lon, max_lat, max_lon = bounds
    if min_lat > max_lat:
        min_lat, max_lat = max_lat, min_lat
    if not (min_lat <= lat <= max_lat):
        return False
    if min_lon <= max_lon:
        return min_lon <= lon <= max_lon
    else:
        # Crosses the antimeridian (+180 / -180)
        return lon >= min_lon or lon <= max_lon


class OsirisIntelClient:
    """Singleton client managing connectivity to OSIRIS Global Intelligence."""
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(OsirisIntelClient, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, base_url: Optional[str] = None):
        if getattr(self, "_initialized", False):
            return
        self.base_url = (base_url or os.environ.get("OSIRIS_API_URL") or DEFAULT_OSIRIS_URL).rstrip("/")
        self._cache_mem: Dict[str, Tuple[float, Any]] = {}
        self._endpoint_failures: Dict[str, float] = {}
        self._last_warn_log: Dict[str, float] = {}
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._initialized = True

    # -------------------------------------------------------------------------
    # Caching & HTTP Transport
    # -------------------------------------------------------------------------

    def _get_cache(self, key: str, ttl: float, allow_stale: bool = False) -> Optional[Any]:
        """Check in-memory and disk cache for fresh response, or stale response during network outages."""
        now = time.time()
        # 1. Memory check
        if key in self._cache_mem:
            exp, data = self._cache_mem[key]
            if allow_stale or now < exp:
                return data

        # 2. Disk check
        disk_path = CACHE_DIR / f"{key}.json"
        if disk_path.exists():
            try:
                with open(disk_path, "r", encoding="utf-8") as f:
                    entry = json.load(f)
                    if allow_stale or now < entry.get("expires_at", 0):
                        self._cache_mem[key] = (entry.get("expires_at", now + ttl), entry["data"])
                        return entry["data"]
            except Exception as e:
                logger.debug(f"[OSIRIS] Cache read failed for {key}: {e}")

        return None

    def _set_cache(self, key: str, data: Any, ttl: float):
        """Save response to in-memory and disk cache."""
        exp = time.time() + ttl
        self._cache_mem[key] = (exp, data)
        disk_path = CACHE_DIR / f"{key}.json"
        try:
            with open(disk_path, "w", encoding="utf-8") as f:
                json.dump({"expires_at": exp, "data": data}, f)
        except Exception as e:
            logger.debug(f"[OSIRIS] Cache write failed for {key}: {e}")

    def clear_cache(self):
        """Clear in-memory and on-disk response caches for testing and forced reload."""
        self._cache_mem.clear()
        if hasattr(self, '_endpoint_failures'):
            self._endpoint_failures.clear()
        if hasattr(self, '_last_warn_log'):
            self._last_warn_log.clear()
        try:
            for p in CACHE_DIR.glob("*.json"):
                p.unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"[OSIRIS] Cache clear notice: {e}")

    def _fetch_endpoint(self, path: str, params: Optional[Dict[str, Any]] = None, ttl: float = 30) -> Optional[Any]:
        """Perform HTTP GET against an OSIRIS endpoint with caching, backoff, and error protection."""
        url = f"{self.base_url}{path}"
        if params:
            query_str = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if query_str:
                url = f"{url}?{query_str}"

        safe_key = urllib.parse.quote(f"{self.base_url}_{path}_{json.dumps(params or {}, sort_keys=True)}", safe="")[:120]
        cached = self._get_cache(safe_key, ttl)
        if cached is not None:
            return cached

        now = time.time()
        # Fast failure backoff: don't stall event loops with repeated timeouts when upstream is down
        if now < self._endpoint_failures.get(path, 0):
            stale = self._get_cache(safe_key, ttl, allow_stale=True)
            if stale is not None:
                return stale
            return None

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) JARVIS-Tactical/2.0",
                "Accept": "application/json, text/plain, */*"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                self._set_cache(safe_key, data, ttl)
                self._endpoint_failures.pop(path, None)
                return data
        except Exception as e:
            self._endpoint_failures[path] = time.time() + 60.0
            stale = self._get_cache(safe_key, ttl, allow_stale=True)
            if stale is not None:
                logger.debug(f"[OSIRIS] Serving stale cached telemetry for {url} following upstream error: {e}")
                return stale

            last_log = self._last_warn_log.get(path, 0)
            if now - last_log > 120.0:
                self._last_warn_log[path] = now
                logger.warning(f"[OSIRIS] Request failed for {url}: {e} (entering 60s contingency mode)")
            else:
                logger.debug(f"[OSIRIS] Request failed for {url} (backoff active): {e}")
            return None

    # -------------------------------------------------------------------------
    # Core Telemetry & Intelligence Endpoints
    # -------------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Retrieve live aggregate counters across all OSIRIS streams."""
        res = self._fetch_endpoint("/api/stats", ttl=DEFAULT_TTLS["stats"])
        if res and "stats" in res:
            return res["stats"]
        return {"flights": 0, "sats": 0, "weather": 0, "nuclear": 0, "incidents": 0}

    def get_flights(
        self,
        military_only: bool = False,
        bounds: Optional[Any] = None,
        category: Optional[str] = None,
        limit: Optional[int] = None,
        return_meta: bool = False,
    ) -> Dict[str, Any]:
        """
        Ingest real-time global flights, separating military and commercial tracks.
        Supports bounding-box filtering, category filtering ('military', 'commercial', 'private', 'jamming', 'all'),
        display caps, and explicit degradation metadata for edge cases.
        """
        parsed_bounds = parse_bounds(bounds)
        res = self._fetch_endpoint("/api/flights", ttl=DEFAULT_TTLS["flights"])

        cat_clean = (category or "").lower().strip()
        if military_only and not cat_clean:
            cat_clean = "military"

        if not res:
            # Fallback to offline fixtures if available
            contingency_mil = []
            try:
                from modules.flight_intel import OFFLINE_MIL_FIXTURES
                for f in OFFLINE_MIL_FIXTURES:
                    f_lat = f.get("lat")
                    f_lon = f.get("lon", f.get("lng"))
                    if parsed_bounds and not is_point_in_bounds(f_lat, f_lon, parsed_bounds):
                        continue
                    contingency_mil.append(f)
            except Exception:
                pass

            return {
                "status": "upstream_error",
                "total": len(contingency_mil),
                "military": contingency_mil,
                "commercial": [],
                "private": [],
                "gps_jamming": [],
                "capped": False,
                "debrief": "OSIRIS flight stream offline. Offline military fixtures active." if contingency_mil else "OSIRIS flight telemetry stream unavailable.",
                "bounds": bounds,
            }

        mil = list(res.get("military_flights", []))
        com = list(res.get("commercial_flights", []))
        priv = list(res.get("private_flights", []))
        jam = list(res.get("gps_jamming", []))

        # Bounding box filter
        if parsed_bounds:
            mil = [f for f in mil if is_point_in_bounds(f.get("lat"), f.get("lng", f.get("lon")), parsed_bounds)]
            com = [f for f in com if is_point_in_bounds(f.get("lat"), f.get("lng", f.get("lon")), parsed_bounds)]
            priv = [f for f in priv if is_point_in_bounds(f.get("lat"), f.get("lng", f.get("lon")), parsed_bounds)]
            jam = [j for j in jam if is_point_in_bounds(j.get("lat"), j.get("lng", j.get("lon")), parsed_bounds)]

        # Category filter
        if cat_clean in ("military", "mil"):
            com, priv, jam = [], [], []
        elif cat_clean in ("commercial", "civ", "civilian"):
            mil, priv, jam = [], [], []
        elif cat_clean in ("private", "vip", "pia"):
            mil, com, jam = [], [], []
        elif cat_clean in ("jamming", "gps_jamming", "ew"):
            mil, com, priv = [], [], []

        total_matched = len(mil) + len(com) + len(priv)
        capped = False
        if limit and limit > 0 and total_matched > limit:
            capped = True
            remaining = limit
            mil = mil[:remaining]
            remaining = max(0, remaining - len(mil))
            com = com[:remaining]
            remaining = max(0, remaining - len(com))
            priv = priv[:remaining]

        # Status and debrief for edge cases
        if total_matched == 0 and len(jam) == 0:
            status = "zero_results"
            debrief = "Zero aircraft or GPS jamming sectors detected within active viewport."
        elif capped:
            status = "capped"
            debrief = f"Airspace track density high: showing {len(mil) + len(com) + len(priv)} of {total_matched} contacts (cap: {limit})."
        else:
            status = "ok"
            debrief = f"Airspace radar active: {len(mil)} military, {len(com)} commercial, {len(priv)} private, {len(jam)} jamming sectors."

        return {
            "status": status,
            "total": len(mil) + len(com) + len(priv),
            "military": mil,
            "commercial": com,
            "private": priv,
            "gps_jamming": jam,
            "capped": capped,
            "limit": limit,
            "total_matched": total_matched,
            "debrief": debrief,
            "bounds": bounds,
        }

    def get_satellites(
        self,
        query: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 1500,
        bounds: Optional[Any] = None,
        return_meta: bool = False,
    ) -> Any:
        """
        Retrieve tracked satellites (18,800+ orbital objects) with real-time TLE positions.
        Supports category filtering (stations, nav/gps, geo, starlink/comms, visual, recon, all),
        sub-satellite ground-track bounding-box filtering, high-density GPU collection scaling (1500+),
        and graceful multi-regime orbital contingency fallback.
        """
        parsed_bounds = parse_bounds(bounds)
        res = self._fetch_endpoint("/api/satellites", ttl=DEFAULT_TTLS["satellites"])

        if not res or "satellites" not in res:
            # Contingency satellite fixtures across LEO, MEO, and GEO regimes (God's Eye Parity)
            contingency_sats = [
                # Stations (LEO)
                {"name": "ISS (ZARYA)", "lat": 25.5, "lng": -45.2, "alt": 418, "mission": "Human Spaceflight Research Laboratory", "category": "stations", "noradId": "25544", "source": "Contingency Ephemeris"},
                {"name": "TIANGONG (CSS)", "lat": 18.2, "lng": 110.5, "alt": 389, "mission": "Chinese Space Station", "category": "stations", "noradId": "48274", "source": "Contingency Ephemeris"},
                # Navigation (MEO ~20,000 km)
                {"name": "NAVSTAR GPS USA-203", "lat": 32.1, "lng": -105.4, "alt": 20180, "mission": "Global Positioning System", "category": "nav", "noradId": "34661", "source": "Contingency Ephemeris"},
                {"name": "GPS BIIF-12 (USA-266)", "lat": -12.4, "lng": 45.8, "alt": 20200, "mission": "GPS Block IIF Navigation", "category": "nav", "noradId": "41328", "source": "Contingency Ephemeris"},
                {"name": "GLONASS-M 755", "lat": 44.5, "lng": 82.1, "alt": 19130, "mission": "Russian Navigation Constellation", "category": "nav", "noradId": "41554", "source": "Contingency Ephemeris"},
                {"name": "GALILEO GSAT-0205", "lat": -28.9, "lng": -32.5, "alt": 23222, "mission": "European Galileo Navigation", "category": "nav", "noradId": "40889", "source": "Contingency Ephemeris"},
                # Geostationary (GEO ~35,786 km)
                {"name": "GOES-16 (EAST)", "lat": 0.0, "lng": -75.2, "alt": 35786, "mission": "NOAA Geostationary Weather Satellite", "category": "geo", "noradId": "41866", "source": "Contingency Ephemeris"},
                {"name": "METEOSAT-11", "lat": 0.0, "lng": 0.0, "alt": 35790, "mission": "EUMETSAT Geostationary Earth Observation", "category": "geo", "noradId": "40732", "source": "Contingency Ephemeris"},
                {"name": "INMARSAT 5-F4", "lat": 0.0, "lng": 54.5, "alt": 35785, "mission": "Global Xpress Broadband Comms", "category": "geo", "noradId": "42702", "source": "Contingency Ephemeris"},
                # Visual / Science (LEO)
                {"name": "HST (HUBBLE)", "lat": -15.4, "lng": -140.2, "alt": 535, "mission": "Hubble Space Telescope", "category": "visual", "noradId": "20580", "source": "Contingency Ephemeris"},
                {"name": "TERRA (EOS AM-1)", "lat": 68.2, "lng": -92.4, "alt": 705, "mission": "NASA Earth Observing System", "category": "visual", "noradId": "25994", "source": "Contingency Ephemeris"},
                # Comms / Starlink Shell
                {"name": "STARLINK-3101", "lat": 42.1, "lng": -65.2, "alt": 550, "mission": "Starlink Broadband Constellation", "category": "starlink", "noradId": "49001", "source": "Contingency Ephemeris"},
                {"name": "STARLINK-3102", "lat": -38.5, "lng": 142.1, "alt": 550, "mission": "Starlink Broadband Constellation", "category": "starlink", "noradId": "49002", "source": "Contingency Ephemeris"},
                {"name": "ONEWEB-0128", "lat": 72.0, "lng": 18.5, "alt": 1200, "mission": "OneWeb Broadband LEO Shell", "category": "starlink", "noradId": "45250", "source": "Contingency Ephemeris"},
            ]
            for s in contingency_sats:
                s["simulated"] = True
                s["badge"] = "SIMULATED"
            if return_meta:
                return {
                    "status": "upstream_error",
                    "count": len(contingency_sats[:limit]),
                    "total_matched": len(contingency_sats),
                    "capped": False,
                    "limit": limit,
                    "satellites": contingency_sats[:limit],
                    "debrief": "OSIRIS satellite telemetry synchronized via contingency orbital ephemeris.",
                    "category": category,
                    "bounds": bounds,
                }
            return contingency_sats[:limit]

        sats: List[Dict[str, Any]] = res.get("satellites", [])
        filtered = []

        q_lower = (query or "").lower().strip()
        cat_lower = (category or "").lower().strip()

        # Category keyword matchers for operator-selected constellations:
        # stations, nav/gps, geo, starlink/comms, visual, recon
        for sat in sats:
            s_name = str(sat.get("name", "")).lower()
            s_cat = str(sat.get("category", "")).lower()
            s_mission = str(sat.get("mission", "")).lower()
            s_lat = sat.get("lat")
            s_lng = sat.get("lng", sat.get("lon"))
            s_alt = float(sat.get("alt") or 0)

            # Bounding box filter (sub-satellite point)
            if parsed_bounds and not is_point_in_bounds(s_lat, s_lng, parsed_bounds):
                continue

            # Query keyword filter
            if q_lower and (q_lower not in s_name and q_lower not in s_mission and q_lower not in s_cat):
                continue

            # Category filter (God's Eye SATELLITE_CLASSES taxonomy + specific vehicle queries)
            if cat_lower and cat_lower not in ("all", "*"):
                if cat_lower == "iss":
                    if not ("iss" in s_name or "zarya" in s_name):
                        continue
                elif cat_lower == "tiangong":
                    if not ("tiangong" in s_name or "css" in s_name or "tianhe" in s_name):
                        continue
                elif cat_lower in ("stations", "station"):
                    if not ("iss" in s_name or "zarya" in s_name or "tiangong" in s_name or "css" in s_name or "station" in s_cat or "station" in s_mission):
                        continue
                elif cat_lower in ("nav", "gps", "gnss", "navigation", "glonass", "galileo", "beidou"):
                    if not ("gps" in s_name or "navstar" in s_name or "glonass" in s_name or "galileo" in s_name or "beidou" in s_name or "gnss" in s_name or "nav" in s_cat):
                        continue
                elif cat_lower in ("geo", "geostationary", "geosynchronous"):
                    if not ("geo" in s_cat or s_alt > 34000 or "goes" in s_name or "meteosat" in s_name or "inmarsat" in s_name):
                        continue
                elif cat_lower in ("starlink", "comms", "broadband", "oneweb"):
                    if not ("starlink" in s_name or "starlink" in s_cat or "oneweb" in s_name or "iridium" in s_name):
                        continue
                elif cat_lower in ("visual", "bright", "science"):
                    if not ("visual" in s_cat or "hst" in s_name or "hubble" in s_name or "terra" in s_name or "aqua" in s_name or "landsat" in s_name):
                        continue
                elif cat_lower in ("recon", "military", "surveillance"):
                    is_nav = ("gps" in s_name or "navstar" in s_name or "glonass" in s_name or "navigation" in s_mission or s_cat in ("gps", "navigation"))
                    if is_nav:
                        continue
                    if not ("recon" in s_name or "military" in s_name or "usa-" in s_name or "kh-" in s_name or "cosmos" in s_name or "nrol" in s_name or "spy" in s_mission or s_cat in ("recon", "military", "surveillance")):
                        continue
                else:
                    if cat_lower not in s_cat and cat_lower not in s_name and cat_lower not in s_mission:
                        continue

            filtered.append(sat)

        total_matched = len(filtered)
        capped = total_matched > limit
        sliced = filtered[:limit]

        if return_meta:
            if total_matched == 0:
                status = "zero_results"
                debrief = f"Zero orbital passes detected for category '{category or query or 'all'}' in specified zone."
            elif capped:
                status = "capped"
                debrief = f"Tracking {len(sliced)} of {total_matched} orbital assets (display cap: {limit})."
            else:
                status = "ok"
                debrief = f"Orbital telemetry active: {len(sliced)} assets tracked for '{category or query or 'all'}'."

            return {
                "status": status,
                "count": len(sliced),
                "total_matched": total_matched,
                "capped": capped,
                "limit": limit,
                "satellites": sliced,
                "debrief": debrief,
                "category": category,
                "bounds": bounds,
            }

        return sliced

    def get_conflicts(
        self,
        bounds: Optional[Any] = None,
        severity: Optional[str] = None,
        limit: Optional[int] = None,
        return_meta: bool = False,
    ) -> Dict[str, Any]:
        """
        Retrieve active warzones, live frontlines, and conflict events.
        Supports bounding-box viewport filtering, severity filtering, and graceful degradation.
        """
        parsed_bounds = parse_bounds(bounds)
        res = self._fetch_endpoint("/api/conflicts", ttl=DEFAULT_TTLS["conflicts"])

        if not res:
            contingency_zones = [
                {
                    "id": "contingency-ukraine",
                    "label": "UKRAINE THEATRE (CONTINGENCY)",
                    "severity": "war",
                    "lat": 48.3794,
                    "lng": 31.1656,
                    "description": "Active conflict zone — frontline monitoring active.",
                    "status": "active",
                },
                {
                    "id": "contingency-mideast",
                    "label": "MIDDLE EAST SECTOR (CONTINGENCY)",
                    "severity": "crisis",
                    "lat": 31.7683,
                    "lng": 35.2137,
                    "description": "Heightened tactical alert and air defense posture.",
                    "status": "active",
                },
            ]
            for z in contingency_zones:
                z["simulated"] = True
                z["badge"] = "SIMULATED"
            if parsed_bounds:
                contingency_zones = [z for z in contingency_zones if is_point_in_bounds(z.get("lat"), z.get("lng", z.get("lon")), parsed_bounds)]

            return {
                "status": "upstream_error",
                "totalZones": len(contingency_zones),
                "activeWarzones": len([z for z in contingency_zones if z.get("severity") == "war"]),
                "zones": contingency_zones,
                "liveEvents": [],
                "capped": False,
                "debrief": "OSIRIS conflict link offline. Displaying tactical contingency zones.",
                "bounds": bounds,
            }

        zones = list(res.get("zones", []))
        live_events = list(res.get("liveEvents", []))

        if parsed_bounds:
            zones = [z for z in zones if is_point_in_bounds(z.get("lat"), z.get("lng", z.get("lon")), parsed_bounds)]
            live_events = [e for e in live_events if is_point_in_bounds(e.get("lat"), e.get("lng", e.get("lon")), parsed_bounds)]

        if severity:
            sev_clean = severity.lower().strip()
            zones = [z for z in zones if z.get("severity", "").lower() == sev_clean]

        if parsed_bounds or severity:
            total_zones = len(zones)
            active_warzones = len([z for z in zones if z.get("severity", "").lower() == "war"])
        else:
            total_zones = res.get("totalZones", len(zones))
            active_warzones = res.get("activeWarzones", len([z for z in zones if z.get("severity", "").lower() == "war"]))

        capped = False
        if limit and limit > 0 and len(zones) > limit:
            capped = True
            zones = zones[:limit]

        if total_zones == 0 and len(live_events) == 0:
            status = "zero_results"
            debrief = "Zero active conflict zones or tactical incidents detected in sector."
        elif capped:
            status = "capped"
            debrief = f"Conflict intelligence: showing {len(zones)} of {total_zones} zones in sector (cap: {limit})."
        else:
            status = "ok"
            debrief = f"Tactical intelligence active: {active_warzones} active warzones, {total_zones} conflict sectors tracked."

        return {
            "status": status,
            "totalZones": total_zones,
            "activeWarzones": active_warzones,
            "zones": zones,
            "liveEvents": live_events,
            "capped": capped,
            "limit": limit,
            "debrief": debrief,
            "bounds": bounds,
        }

    def get_frontlines(self) -> Dict[str, Any]:
        """Retrieve frontline polygon geometry for active theatres."""
        res = self._fetch_endpoint("/api/frontlines", ttl=DEFAULT_TTLS["conflicts"])
        return res or {"frontlines": {}}

    def get_turn_by_turn_route(
        self,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
        mode: str = "auto"
    ) -> Optional[Dict[str, Any]]:
        """
        Query OSIRIS Valhalla/OSRM turn-by-turn routing engine for true road geometry.
        """
        params = {
            "from": f"{from_lat},{from_lon}",
            "to": f"{to_lat},{to_lon}",
            "mode": mode
        }
        res = self._fetch_endpoint("/api/directions", params=params, ttl=DEFAULT_TTLS["directions"])
        if not res:
            return None

        return res

    def get_cyber_recon(self, target: str, lookup_type: str = "all") -> Dict[str, Any]:
        """
        Perform fast OSINT reconnaissance via OSIRIS RECON toolkit endpoints.
        """
        results: Dict[str, Any] = {"target": target, "timestamp": time.time()}

        # 1. IP / Domain Intelligence
        is_ip = target.replace(".", "").isdigit()
        if is_ip:
            ip_res = self._fetch_endpoint(f"/api/osint/ip", params={"ip": target}, ttl=DEFAULT_TTLS["osint"])
            if ip_res:
                results["ip_info"] = ip_res
        else:
            dns_res = self._fetch_endpoint(f"/api/osint/dns", params={"domain": target}, ttl=DEFAULT_TTLS["osint"])
            if dns_res:
                results["dns"] = dns_res

            certs_res = self._fetch_endpoint(f"/api/osint/certs", params={"domain": target}, ttl=DEFAULT_TTLS["osint"])
            if certs_res:
                results["certificates"] = certs_res

            whois_res = self._fetch_endpoint(f"/api/osint/whois", params={"domain": target}, ttl=DEFAULT_TTLS["osint"])
            if whois_res:
                results["whois"] = whois_res

        # 2. Shodan Vulnerabilities
        shodan_res = self._fetch_endpoint(f"/api/osint/shodan", params={"host": target}, ttl=DEFAULT_TTLS["osint"])
        if shodan_res:
            results["shodan"] = shodan_res

        # 3. Sanctions check
        sanctions_res = self._fetch_endpoint(f"/api/osint/sanctions", params={"query": target}, ttl=DEFAULT_TTLS["osint"])
        if sanctions_res:
            results["sanctions"] = sanctions_res

        return results

    def get_live_news(self) -> List[Dict[str, Any]]:
        """Retrieve 24/7 global SIGINT broadcast streams."""
        res = self._fetch_endpoint("/api/live-news", ttl=DEFAULT_TTLS["news"])
        if res and "feeds" in res:
            return res["feeds"]
        return []

    def get_space_weather(self) -> Dict[str, Any]:
        """Retrieve solar flare activity and geomagnetic conditions from NOAA SWPC."""
        res = self._fetch_endpoint("/api/space-weather", ttl=DEFAULT_TTLS["space_weather"])
        return res or {}

    def get_maritime(self) -> Dict[str, Any]:
        """Retrieve maritime ports, strategic chokepoints, and vessel traffic."""
        res = self._fetch_endpoint("/api/maritime", ttl=DEFAULT_TTLS["maritime"])
        return res or {"ports": [], "chokepoints": [], "ships": []}

    def get_infrastructure(self) -> List[Dict[str, Any]]:
        """Retrieve critical infrastructure: nuclear facilities and power plants."""
        res = self._fetch_endpoint("/api/infrastructure", ttl=DEFAULT_TTLS["infrastructure"])
        if res and isinstance(res, list):
            return res
        return []

    # -------------------------------------------------------------------------
    # Utilities
    # -------------------------------------------------------------------------

    @staticmethod
    def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate great circle distance in kilometers between two coordinates."""
        R = 6371.0  # Earth radius in kilometers
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c


# Global Singleton Accessor
_osiris_client: Optional[OsirisIntelClient] = None

def get_osiris_client() -> OsirisIntelClient:
    global _osiris_client
    if _osiris_client is None:
        _osiris_client = OsirisIntelClient()
    return _osiris_client
