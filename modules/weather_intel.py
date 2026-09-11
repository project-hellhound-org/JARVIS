# modules/weather_intel.py
import json
import re
import time
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional

WMO_WEATHER_CODES = {
    0: 'Clear sky',
    1: 'Mainly clear',
    2: 'Partly cloudy',
    3: 'Overcast',
    45: 'Foggy conditions',
    48: 'Depositing rime fog',
    51: 'Light drizzle',
    53: 'Moderate drizzle',
    55: 'Dense drizzle',
    61: 'Slight rain',
    63: 'Moderate rain',
    65: 'Heavy rain',
    71: 'Slight snowfall',
    73: 'Moderate snowfall',
    75: 'Heavy snowfall',
    80: 'Slight rain showers',
    81: 'Moderate rain showers',
    82: 'Violent rain showers',
    95: 'Thunderstorm',
    96: 'Thunderstorm with slight hail',
    99: 'Thunderstorm with heavy hail'
}

OFFLINE_WEATHER_FIXTURE = {
    'city': 'Tactical Operations Area',
    'lat': 40.7128,
    'lon': -74.0060,
    'temp_c': 21.4,
    'temp_f': 70.5,
    'feels_like_c': 21.0,
    'feels_like_f': 69.8,
    'humidity': 54,
    'wind_kmh': 14.2,
    'wind_dir': 230,
    'cloud_cover': 25,
    'pressure_hpa': 1014.2,
    'condition': 'Partly cloudy',
    'precipitation_mm': 0.0,
    'source': 'offline_contingency'
}

class WeatherIntelEngine:
    """
    Real-time Live Weather & Atmospheric Telemetry Engine.
    Uses Open-Meteo keyless API with geocoding resolution and offline contingency.
    """
    def __init__(self):
        self.cache_ttl = 300  # 5 minutes
        self._cache: Dict[str, Any] = {}
        self._local_sector_cache: Optional[Dict[str, Any]] = None

    def _resolve_local_sector(self) -> Dict[str, Any]:
        """
        Resolve user's actual local sector.
        1. Check config.yaml for home_sector
        2. Fast IP geolocation probe with cached result
        3. Fallback to Kotagiri, Nilgiris, Tamil Nadu, India (11.4228, 76.8661)
        """
        if self._local_sector_cache:
            return self._local_sector_cache

        # 1. Check config.yaml
        try:
            import yaml
            if os.path.exists('config.yaml'):
                with open('config.yaml', 'r') as f:
                    cfg = yaml.safe_load(f) or {}
                    hs = cfg.get('home_sector')
                    if isinstance(hs, dict) and 'lat' in hs and 'lon' in hs:
                        res = {
                            'city': hs.get('city', 'Kotagiri'),
                            'lat': float(hs['lat']),
                            'lon': float(hs['lon']),
                            'country': hs.get('country', 'IN')
                        }
                        self._local_sector_cache = res
                        return res
        except Exception:
            pass

        # 2. Fast IP Geolocation Probe (1.8s timeout)
        try:
            req = urllib.request.Request('https://ipapi.co/json/', headers={'User-Agent': 'JARVIS-OSINT/2.0'})
            with urllib.request.urlopen(req, timeout=1.8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('latitude') and data.get('longitude'):
                    city = data.get('city') or data.get('region') or 'Local Sector'
                    res = {
                        'city': city,
                        'lat': float(data['latitude']),
                        'lon': float(data['longitude']),
                        'country': data.get('country_name') or data.get('country') or 'IN'
                    }
                    self._local_sector_cache = res
                    return res
        except Exception:
            pass

        # 3. Default fallback: Home Sector Kotagiri, Nilgiris
        res = {'city': 'Kotagiri', 'lat': 11.4228, 'lon': 76.8661, 'country': 'IN'}
        self._local_sector_cache = res
        return res

    def resolve_location(self, location_query: str) -> Optional[Dict[str, Any]]:
        """
        Resolve location name to lat/lon using local gazetteer or Open-Meteo geocoding.
        """
        loc_clean = location_query.strip().lower()
        if not loc_clean or loc_clean in ('local', 'here', 'my location', 'current location', 'home', 'our sector', 'local sector'):
            return self._resolve_local_sector()

        # 1. Try local gazetteer
        try:
            from modules.geocode import geocode
            res = geocode(loc_clean)
            if res:
                return {
                    'city': loc_clean.title(),
                    'lat': res['lat'],
                    'lon': res['lng'],
                    'precision': res.get('precision', 'city')
                }
        except Exception:
            pass

        # 2. Try Open-Meteo Geocoding API
        try:
            url = f'https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(loc_clean)}&count=1&language=en&format=json'
            req = urllib.request.Request(url, headers={'User-Agent': 'JARVIS-WeatherIntel/2.0'})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                results = data.get('results', [])
                if results:
                    top = results[0]
                    return {
                        'city': top.get('name', loc_clean.title()),
                        'country': top.get('country', ''),
                        'lat': top.get('latitude', 0.0),
                        'lon': top.get('longitude', 0.0)
                    }
        except Exception:
            pass

        return None

    def get_weather(self, location_query: str = '') -> Dict[str, Any]:
        """
        Get current weather conditions for a specified location.
        """
        target_loc = self.resolve_location(location_query)
        if not target_loc:
            fixture = dict(OFFLINE_WEATHER_FIXTURE)
            if location_query:
                fixture['city'] = location_query.title()
            return fixture

        lat = target_loc['lat']
        lon = target_loc['lon']
        city_name = target_loc.get('city', location_query.title() or 'Local Sector')

        cache_key = f'{round(lat, 2)},{round(lon, 2)}'
        now = time.time()
        if cache_key in self._cache:
            entry, fetch_time = self._cache[cache_key]
            if (now - fetch_time) < self.cache_ttl:
                return entry

        url = (
            f'https://api.open-meteo.com/v1/forecast?'
            f'latitude={lat}&longitude={lon}&'
            f'current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,cloud_cover,surface_pressure,wind_speed_10m,wind_direction_10m&'
            f'wind_speed_unit=kmh&timezone=auto'
        )

        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'JARVIS-WeatherIntel/2.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                curr = data.get('current', {})
                code = curr.get('weather_code', 0)
                condition = WMO_WEATHER_CODES.get(code, 'Normal conditions')
                temp_c = curr.get('temperature_2m', 20.0)
                temp_f = round((temp_c * 9/5) + 32, 1)
                feels_c = curr.get('apparent_temperature', temp_c)
                feels_f = round((feels_c * 9/5) + 32, 1)

                weather_res = {
                    'city': city_name,
                    'lat': lat,
                    'lon': lon,
                    'temp_c': temp_c,
                    'temp_f': temp_f,
                    'feels_like_c': feels_c,
                    'feels_like_f': feels_f,
                    'humidity': curr.get('relative_humidity_2m', 50),
                    'wind_kmh': curr.get('wind_speed_10m', 0.0),
                    'wind_dir': curr.get('wind_direction_10m', 0),
                    'cloud_cover': curr.get('cloud_cover', 0),
                    'pressure_hpa': curr.get('surface_pressure', 1013.2),
                    'condition': condition,
                    'precipitation_mm': curr.get('precipitation', 0.0),
                    'source': 'open-meteo'
                }
                self._cache[cache_key] = (weather_res, now)
                return weather_res
        except Exception:
            pass

        # Offline fallback
        fallback = dict(OFFLINE_WEATHER_FIXTURE)
        fallback['city'] = city_name
        fallback['lat'] = lat
        fallback['lon'] = lon
        return fallback

    def format_weather_debrief(self, w: Dict[str, Any]) -> str:
        """
        Format authentic J.A.R.V.I.S. weather debrief.
        """
        city = w.get('city', 'Current Sector')
        cond = w.get('condition', 'Overcast')
        temp_f = w.get('temp_f', 70)
        temp_c = w.get('temp_c', 21)
        wind = w.get('wind_kmh', 15)
        humidity = w.get('humidity', 50)

        return (
            f"Weather check for {city}: It's {cond}, sitting at {temp_f}°F ({temp_c}°C). "
            f"Winds clocking at {wind} km/h with {humidity}% humidity. Good enough to operate, partner."
        )
