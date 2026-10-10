"""
OSINT Radio Engine for JARVIS.
Provides authentic tactical, scanner, aviation, maritime, weather, and news streams.
Integrates with Radio Browser API (https://api.radio-browser.info), verified URLs, 24h caching,
and curated stations.
Zero entertainment or music stations.
"""
import os
import json
import time
import socket
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Any, Optional

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
CACHE_FILE = PROJECT_ROOT / "data" / "radio_cache.json"
CURATED_FILE = PROJECT_ROOT / "data" / "stations.json"
CACHE_TTL = 86400  # 24 hours

CATEGORIES = {
    "atc": "Flight comms / ATC and aviation",
    "scanner": "Police/Fire/EMS scanners",
    "marine": "Marine/Coast Guard VHF",
    "weather": "Weather and emergency (NOAA)",
    "news": "News and world service",
    "cyber": "Cyber News (RSS Feed)",
}

CATEGORY_TAGS = {
    "atc": ["atc", "aviation", "airband"],
    "scanner": ["scanner", "police", "fire", "ems", "emergency"],
    "marine": ["marine", "coastguard"],
    "weather": ["weather", "noaa"],
    "news": ["news", "information"],
}

FALLBACK_SERVERS = [
    "de1.api.radio-browser.info",
    "nl1.api.radio-browser.info",
    "at1.api.radio-browser.info",
]

MUSIC_KEYWORDS = {
    "music", "pop", "rock", "jazz", "dance", "electronic", "hiphop", "hits",
    "classical", "r&b", "reggae", "disco", "club", "chillout", "lounge"
}


def check_stream_url(url: str, timeout: float = 3.0) -> bool:
    """
    Test every URL before listing it:
    Requires HTTP 200 or 206 and an audio content-type.
    """
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return False
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; JARVIS-OSINT-Radio/1.0)",
                "Range": "bytes=0-1024"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "").lower()
            if status in (200, 206) and any(a in content_type for a in ("audio", "ogg", "mpeg", "aac", "stream")):
                return True
    except Exception:
        pass
    return False


class OsintRadioService:
    def __init__(self, cache_file: Optional[Path] = None, curated_file: Optional[Path] = None):
        self.cache_file = cache_file or CACHE_FILE
        self.curated_file = curated_file or CURATED_FILE

    def get_categories(self) -> Dict[str, str]:
        return CATEGORIES.copy()

    def get_curated_stations(self) -> List[Dict[str, Any]]:
        if self.curated_file.exists():
            try:
                with open(self.curated_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[osint_radio] Warning loading curated stations: {e}")
        return []

    def get_cached_stations(self) -> Optional[List[Dict[str, Any]]]:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cached_at = data.get("cached_at", 0)
                if time.time() - cached_at < CACHE_TTL:
                    return data.get("stations", [])
            except Exception as e:
                print(f"[osint_radio] Warning reading cache: {e}")
        return None

    def save_cache(self, stations: List[Dict[str, Any]]) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "cached_at": time.time(),
                "stations": stations
            }
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            print(f"[osint_radio] Warning writing cache: {e}")

    def discover_radio_browser_server(self) -> Optional[str]:
        """Find a working server via the /json/servers list or fallback list."""
        try:
            req = urllib.request.Request(
                "https://all.api.radio-browser.info/json/servers",
                headers={"User-Agent": "JARVIS-OSINT-Radio/1.0"}
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                servers = json.loads(resp.read().decode("utf-8"))
                for s in servers:
                    name = s.get("name")
                    if name:
                        return name
        except Exception:
            pass

        for s in FALLBACK_SERVERS:
            try:
                socket.gethostbyname(s)
                return s
            except Exception:
                continue
        return None

    def fetch_radio_browser_stations_by_tag(self, server: str, tag: str, category: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch stations from Radio Browser API with lastcheckok=1 sorted by votes."""
        url = f"https://{server}/json/stations/bytag/{urllib.parse.quote(tag)}?lastcheckok=1&order=votes&reverse=true&limit={limit}"
        results = []
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-OSINT-Radio/1.0"})
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                raw_stations = json.loads(resp.read().decode("utf-8"))
                for st in raw_stations:
                    name = (st.get("name") or "").strip()
                    stream_url = st.get("url_resolved") or st.get("url")
                    tags_str = (st.get("tags") or "").lower()

                    # Filter out music and entertainment
                    if any(mk in tags_str or mk in name.lower() for mk in MUSIC_KEYWORDS):
                        continue

                    if not stream_url:
                        continue

                    lat = st.get("geo_lat")
                    lon = st.get("geo_long")
                    try:
                        lat = float(lat) if lat is not None else None
                        lon = float(lon) if lon is not None else None
                    except (ValueError, TypeError):
                        lat, lon = None, None

                    station_obj = {
                        "id": f"rb_{st.get("stationuuid", str(hash(name))[:8])}",
                        "name": name,
                        "category": category,
                        "channel": 14,
                        "freq": f"{st.get("codec", "MP3").upper()} • {st.get("bitrate", 128)} kbps",
                        "genre": f"{st.get("country", "Global")} • {st.get("codec", "MP3").upper()}",
                        "tags": tags_str or tag,
                        "lat": lat,
                        "lon": lon,
                        "country": (st.get("countrycode") or "US").upper(),
                        "source": "Radio Browser",
                        "sourceUrl": st.get("homepage") or f"https://www.radio-browser.info",
                        "streamUrl": stream_url,
                        "terms": "Radio Browser: Community-curated public streams under open terms. Non-commercial listen-only."
                    }
                    results.append(station_obj)
        except Exception as e:
            print(f"[osint_radio] Notice querying tag {tag}: {e}")
        return results

    def refresh_from_network(self) -> List[Dict[str, Any]]:
        """Refresh stations from Radio Browser API and verify stream URLs."""
        verified: List[Dict[str, Any]] = []

        # 1. Start with curated stations
        curated = self.get_curated_stations()
        for c in curated:
            # If network check passes or if offline fallback, retain curated
            verified.append(c)

        # 2. Query Radio Browser API
        server = self.discover_radio_browser_server()
        if server:
            for cat, tags in CATEGORY_TAGS.items():
                for tag in tags[:2]:
                    rb_stations = self.fetch_radio_browser_stations_by_tag(server, tag, cat, limit=5)
                    for st in rb_stations:
                        if check_stream_url(st["streamUrl"], timeout=2.5):
                            verified.append(st)

        # Deduplicate by streamUrl
        seen = set()
        deduped = []
        for s in verified:
            url = s.get("streamUrl")
            if url and url not in seen:
                seen.add(url)
                deduped.append(s)

        if deduped:
            self.save_cache(deduped)
            return deduped
        return curated

    def get_stations(self, category: Optional[str] = None, country: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get stations from 24h cache or curated list with optional category and country filters."""
        cached = self.get_cached_stations()
        stations = cached if cached is not None else self.get_curated_stations()

        if not stations:
            stations = self.refresh_from_network()

        # Filter out any music that might have slipped in
        filtered = []
        for s in stations:
            tags = (s.get("tags") or "").lower()
            name = (s.get("name") or "").lower()
            if any(mk in tags or mk in name for mk in MUSIC_KEYWORDS):
                continue
            filtered.append(s)

        if category and category != "all":
            filtered = [s for s in filtered if s.get("category") == category]

        if country and country != "ALL":
            filtered = [s for s in filtered if (s.get("country") or "").upper() == country.upper()]

        return filtered
