# modules/maritime_intel.py
"""
Maritime AIS Vessel Intelligence Engine for J.A.R.V.I.S.
Tracks worldwide commercial shipping, naval task forces, oil tankers, and cargo vessels.
Supports camera area / bounding-box queries, MMSI lookup, and tactical naval monitoring.
"""

import json
import math
import time
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("JarvisMaritimeIntel")

try:
    CACHE_DIR = Path.home() / ".jarvis" / "maritime_cache"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "maritime_cache"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

# Comprehensive High-Fidelity Tactical Maritime Contingency Fleet
TACTICAL_VESSEL_FLEET = [
    # ── Bay of Bengal & South India Littoral Corridor ──
    {
        "id": "vessel-ins-vikrant",
        "name": "INS VIKRANT (R11)",
        "type": "Aircraft Carrier / Naval Escort",
        "lat": 12.8520,
        "lon": 80.4510,
        "speedKnots": 24.2,
        "headingDeg": 145,
        "mmsi": "419001234",
        "imo": "IMO9876543",
        "destination": "CHENNAI NAVAL RADIAL",
        "isMil": True,
        "flag": "IN"
    },
    {
        "id": "vessel-icg-varaha",
        "name": "ICG VARAHA (41)",
        "type": "Offshore Patrol Vessel",
        "lat": 11.8540,
        "lon": 79.9520,
        "speedKnots": 21.0,
        "headingDeg": 20,
        "mmsi": "419000876",
        "imo": "IMO9800876",
        "destination": "PUDUCHERRY COAST",
        "isMil": True,
        "flag": "IN"
    },
    {
        "id": "vessel-msc-eloane",
        "name": "MV MSC ELOANE",
        "type": "Ultra Large Container Vessel",
        "lat": 9.9510,
        "lon": 75.8020,
        "speedKnots": 18.6,
        "headingDeg": 325,
        "mmsi": "352898000",
        "imo": "IMO9755957",
        "destination": "PORT OF KOCHI",
        "isMil": False,
        "flag": "PA"
    },
    {
        "id": "vessel-sea-pioneer",
        "name": "MT SEA PIONEER",
        "type": "Crude Oil Tanker",
        "lat": 7.9540,
        "lon": 77.3510,
        "speedKnots": 12.4,
        "headingDeg": 88,
        "mmsi": "240567000",
        "imo": "IMO9315678",
        "destination": "SINGAPORE STRAIT",
        "isMil": False,
        "flag": "GR"
    },
    {
        "id": "vessel-ins-kolkata",
        "name": "INS KOLKATA (D63)",
        "type": "Guided-Missile Destroyer",
        "lat": 13.2050,
        "lon": 80.6500,
        "speedKnots": 26.0,
        "headingDeg": 160,
        "mmsi": "419000363",
        "imo": "IMO9400363",
        "destination": "BAY OF BENGAL PATROL",
        "isMil": True,
        "flag": "IN"
    },

    # ── Strait of Malacca & Southeast Asia ──
    {
        "id": "vessel-cma-concorde",
        "name": "CMA CGM CONCORDE",
        "type": "LNG Container Vessel",
        "lat": 1.2540,
        "lon": 103.8520,
        "speedKnots": 14.8,
        "headingDeg": 105,
        "mmsi": "228397800",
        "imo": "IMO9839179",
        "destination": "SINGAPORE ANCHORAGE",
        "isMil": False,
        "flag": "FR"
    },
    {
        "id": "vessel-ever-given",
        "name": "EVER GIVEN",
        "type": "Container Ship (20k TEU)",
        "lat": 5.9520,
        "lon": 80.5010,
        "speedKnots": 19.5,
        "headingDeg": 270,
        "mmsi": "353136000",
        "imo": "IMO9811000",
        "destination": "COLOMBO / ROTTERDAM",
        "isMil": False,
        "flag": "PA"
    },
    {
        "id": "vessel-maersk-moller",
        "name": "MAERSK MC-KINNEY MOLLER",
        "type": "Triple-E Container Ship",
        "lat": 2.8500,
        "lon": 101.4000,
        "speedKnots": 17.2,
        "headingDeg": 140,
        "mmsi": "219018271",
        "imo": "IMO9619907",
        "destination": "PORT KLANG",
        "isMil": False,
        "flag": "DK"
    },

    # ── Strait of Hormuz & Persian Gulf ──
    {
        "id": "vessel-front-altair",
        "name": "MT FRONT ALTAIR",
        "type": "VLCC Supertanker",
        "lat": 26.2500,
        "lon": 56.4000,
        "speedKnots": 13.5,
        "headingDeg": 215,
        "mmsi": "538006873",
        "imo": "IMO9745902",
        "destination": "RAS TANURA TERMINAL",
        "isMil": False,
        "flag": "MH"
    },
    {
        "id": "vessel-uss-ford",
        "name": "USS GERALD R FORD (CVN-78)",
        "type": "Nuclear Supercarrier Strike Group",
        "lat": 25.1000,
        "lon": 57.8000,
        "speedKnots": 27.5,
        "headingDeg": 120,
        "mmsi": "369970000",
        "imo": "IMO9978000",
        "destination": "FIFTH FLEET SECTOR",
        "isMil": True,
        "flag": "US"
    },
    {
        "id": "vessel-hms-diamond",
        "name": "HMS DIAMOND (D34)",
        "type": "Type 45 Air Defence Destroyer",
        "lat": 12.6500,
        "lon": 43.4000,
        "speedKnots": 22.0,
        "headingDeg": 340,
        "mmsi": "235008000",
        "imo": "IMO9400034",
        "destination": "BAB-EL-MANDEB MARITIME ESCORT",
        "isMil": True,
        "flag": "GB"
    },

    # ── Taiwan Strait & East Asia ──
    {
        "id": "vessel-shandong-17",
        "name": "CNS SHANDONG (CV-17)",
        "type": "Aircraft Carrier Combat Group",
        "lat": 23.4500,
        "lon": 119.8000,
        "speedKnots": 23.0,
        "headingDeg": 45,
        "mmsi": "412000017",
        "imo": "IMO9900017",
        "destination": "TAIWAN STRAIT RADIAL",
        "isMil": True,
        "flag": "CN"
    },
    {
        "id": "vessel-reagan-76",
        "name": "USS RONALD REAGAN (CVN-76)",
        "type": "Supercarrier Task Force",
        "lat": 34.8000,
        "lon": 139.5000,
        "speedKnots": 25.2,
        "headingDeg": 195,
        "mmsi": "368876000",
        "imo": "IMO9760000",
        "destination": "YOKOSUKA TRANSIT",
        "isMil": True,
        "flag": "US"
    },

    # ── Mediterranean & English Channel ──
    {
        "id": "vessel-cma-antoine",
        "name": "CMA CGM ANTOINE DE SAINT EXUPERY",
        "type": "Mega Container Vessel (20.6k TEU)",
        "lat": 50.4500,
        "lon": -0.8000,
        "speedKnots": 18.0,
        "headingDeg": 65,
        "mmsi": "228339600",
        "imo": "IMO9776418",
        "destination": "LE HAVRE / ROTTERDAM",
        "isMil": False,
        "flag": "FR"
    },
    {
        "id": "vessel-ins-vikramaditya",
        "name": "INS VIKRAMADITYA (R33)",
        "type": "Aircraft Carrier Task Force",
        "lat": 15.2500,
        "lon": 73.2000,
        "speedKnots": 22.8,
        "headingDeg": 175,
        "mmsi": "419000333",
        "imo": "IMO9800333",
        "destination": "KARWAR NAVAL BASE",
        "isMil": True,
        "flag": "IN"
    }
]

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

import os
import threading

def get_aisstream_api_key() -> str:
    key = os.environ.get("AISSTREAM_API_KEY", "").strip()
    if not key:
        try:
            env_file = Path(__file__).resolve().parent.parent / ".env"
            if env_file.exists():
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith("AISSTREAM_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip("\"'")
                        break
        except Exception:
            pass
    return key


class AISStreamClient:
    """
    Real-time AIS vessel telemetry client connected to stream.aisstream.io via WebSocket.
    Tracks global commercial shipping, naval escorts, and cargo vessels.
    """
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or get_aisstream_api_key()
        self._vessels: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
        self._ws = None

    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 5)

    def start(self, bbox: Optional[List[List[float]]] = None):
        if not self.is_configured() or self._running:
            return
        self._running = True
        self._ws_thread = threading.Thread(target=self._run_ws, args=(bbox,), daemon=True)
        self._ws_thread.start()

    def stop(self):
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def _run_ws(self, bbox: Optional[List[List[float]]] = None):
        try:
            import websocket
        except ImportError:
            logger.warning("[AISStream] websocket-client package not installed.")
            return

        ws_url = "wss://stream.aisstream.io/v0/stream"
        sub_msg = {
            "APIKey": self.api_key,
            "BoundingBoxes": bbox or [[[-90, -180], [90, 180]]],
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"]
        }

        while self._running:
            try:
                def on_open(ws):
                    logger.info("[AISStream] Connected to stream.aisstream.io. Sending subscription.")
                    ws.send(json.dumps(sub_msg))

                def on_message(ws, message):
                    try:
                        record = json.loads(message)
                        meta = record.get("MetaData", {})
                        mmsi = str(meta.get("MMSI") or "")
                        if not mmsi:
                            return

                        with self._lock:
                            existing = self._vessels.get(mmsi, {})
                            ship_name = (meta.get("ShipName") or existing.get("name") or f"MMSI {mmsi}").strip()
                            lat = meta.get("latitude")
                            lon = meta.get("longitude")

                            pos_rep = record.get("Message", {}).get("PositionReport", {})
                            sog = pos_rep.get("Sog", existing.get("speedKnots", 0.0))
                            cog = pos_rep.get("Cog", existing.get("headingDeg", 0))
                            true_heading = pos_rep.get("TrueHeading", cog)

                            if lat is None or lon is None:
                                lat = pos_rep.get("Latitude", existing.get("lat"))
                                lon = pos_rep.get("Longitude", existing.get("lon"))

                            if lat is not None and lon is not None:
                                self._vessels[mmsi] = {
                                    "id": f"vessel-{mmsi}",
                                    "name": ship_name,
                                    "type": existing.get("type", "Commercial Vessel"),
                                    "lat": float(lat),
                                    "lon": float(lon),
                                    "speedKnots": float(sog) if sog is not None else 0.0,
                                    "headingDeg": int(true_heading) if true_heading is not None else 0,
                                    "mmsi": mmsi,
                                    "imo": existing.get("imo", ""),
                                    "destination": existing.get("destination", ""),
                                    "isMil": "NAV" in ship_name.upper() or "INS " in ship_name.upper() or "USS " in ship_name.upper(),
                                    "flag": existing.get("flag", "UN"),
                                    "lastSeen": time.time()
                                }
                    except Exception as parse_err:
                        logger.debug(f"[AISStream] Parse error: {parse_err}")

                def on_error(ws, error):
                    logger.debug(f"[AISStream] Error: {error}")

                self._ws = websocket.WebSocketApp(
                    ws_url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.debug(f"[AISStream] Connection loop error: {e}")
            if self._running:
                time.sleep(5)

    def get_vessels(
        self,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        radius_km: Optional[float] = None,
        bounds: Optional[Dict[str, float]] = None,
        vessel_type: Optional[str] = None,
        limit: int = 60
    ) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            stale_keys = [k for k, v in self._vessels.items() if now - v.get("lastSeen", now) > 1800]
            for k in stale_keys:
                del self._vessels[k]
            vessels_snapshot = list(self._vessels.values())

        results = []
        wanted_type = (vessel_type or "").strip().lower()

        for v in vessels_snapshot:
            v_lat = v["lat"]
            v_lon = v["lon"]

            if wanted_type:
                if wanted_type not in v["type"].lower() and wanted_type not in v["name"].lower():
                    continue

            if bounds:
                south = bounds.get("south", -90)
                north = bounds.get("north", 90)
                west = bounds.get("west", -180)
                east = bounds.get("east", 180)
                if not (south <= v_lat <= north and west <= v_lon <= east):
                    continue

            if lat is not None and lon is not None and radius_km is not None:
                dist = haversine_distance(lat, lon, v_lat, v_lon)
                if dist > radius_km:
                    continue
                v_copy = dict(v)
                v_copy["distance_km"] = round(dist, 1)
                results.append(v_copy)
                continue

            results.append(dict(v))

        if lat is not None and lon is not None:
            results.sort(key=lambda x: x.get("distance_km", 0))

        return results[:limit]


class MaritimeIntelClient:
    def __init__(self):
        self._cache = {}
        self.ais_client = AISStreamClient()

    def get_vessels_in_area(
        self,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        radius_km: Optional[float] = None,
        bounds: Optional[Dict[str, float]] = None,
        vessel_type: Optional[str] = None,
        limit: int = 60,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Filter vessels by camera area (lat/lon/radius_km) or bounding box (south, west, north, east).
        If AISSTREAM_API_KEY is absent and not in demo mode, returns clear 'AIS key not configured' status.
        """
        demo_mode = bool(kwargs.get("demo_mode", False))

        if not self.ais_client.is_configured():
            if demo_mode:
                results = []
                wanted_type = (vessel_type or "").strip().lower()
                for v in TACTICAL_VESSEL_FLEET:
                    v_lat, v_lon = v["lat"], v["lon"]
                    if wanted_type and wanted_type not in v["type"].lower() and wanted_type not in v["name"].lower():
                        continue
                    if bounds:
                        if not (bounds.get("south", -90) <= v_lat <= bounds.get("north", 90) and bounds.get("west", -180) <= v_lon <= bounds.get("east", 180)):
                            continue
                    if lat is not None and lon is not None and radius_km is not None:
                        dist = haversine_distance(lat, lon, v_lat, v_lon)
                        if dist > radius_km:
                            continue
                        v_copy = dict(v)
                        v_copy["distance_km"] = round(dist, 1)
                        results.append(v_copy)
                        continue
                    results.append(dict(v))
                if lat is not None and lon is not None:
                    results.sort(key=lambda x: x.get("distance_km", 0))
                final_vessels = results[:limit]
                return {
                    "vessels": final_vessels,
                    "total": len(final_vessels),
                    "source": "tactical_contingency",
                    "key_configured": False,
                    "freshness": "simulated",
                    "center": {"lat": lat, "lon": lon} if (lat and lon) else None,
                    "radius_km": radius_km
                }
            return {
                "vessels": [],
                "total": 0,
                "source": "aisstream",
                "key_configured": False,
                "message": "AIS key not configured",
                "status": "key_missing",
                "freshness": "none",
                "center": {"lat": lat, "lon": lon} if (lat and lon) else None,
                "radius_km": radius_km
            }

        # Key is configured: start client if needed and fetch real AIS vessels
        self.ais_client.start()
        vessels = self.ais_client.get_vessels(
            lat=lat,
            lon=lon,
            radius_km=radius_km,
            bounds=bounds,
            vessel_type=vessel_type,
            limit=limit
        )
        return {
            "vessels": vessels,
            "total": len(vessels),
            "source": "aisstream_live",
            "key_configured": True,
            "freshness": "live",
            "center": {"lat": lat, "lon": lon} if (lat and lon) else None,
            "radius_km": radius_km
        }

    def find_vessel(self, query: str) -> Optional[Dict[str, Any]]:
        """Find a vessel by MMSI, IMO, or name substring."""
        q = (query or "").strip().lower()
        if not q:
            return None
        for v in TACTICAL_VESSEL_FLEET:
            if q == v.get("mmsi", "").lower():
                return v
            if q == v.get("imo", "").lower() or q == v.get("imo", "").replace("IMO", "").lower():
                return v
            if q in v["name"].lower():
                return v
        return None

    def get_all_vessels(self) -> List[Dict[str, Any]]:
        """Return the complete tactical maritime fleet."""
        return list(TACTICAL_VESSEL_FLEET)

_maritime_client: Optional[MaritimeIntelClient] = None

def get_maritime_client() -> MaritimeIntelClient:
    global _maritime_client
    if _maritime_client is None:
        _maritime_client = MaritimeIntelClient()
    return _maritime_client

# Aliases for convenience
MaritimeIntelEngine = MaritimeIntelClient
get_maritime_intel = get_maritime_client
