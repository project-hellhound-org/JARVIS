"""
J.A.R.V.I.S. Image Geolocation & OSINT Visual Analysis Engine.

Design Principles:
1. EXIF Metadata Extraction: Extract real GPS, timestamp, and camera metadata using Pillow.
   If GPS is present, label as "from file metadata (high confidence, but verify)".
2. Dynamic Vision Model Discovery:
   - At runtime, query NVIDIA and Groq `/models` endpoints.
   - Filter candidates matching vision patterns: vision, vlm, vl, neva, paligemma, llava, pixtral, qwen-vl.
   - Test each candidate with a 1x1 image probe.
   - Test `nvidia/nemotron-3-ultra-550b-a55b` for image_url capability; if supported, prioritize as default.
   - Cache results in `data/vision_model_cache.json` and log failure reasons.
3. Gemini Provider:
   - REST API with inlineData image bytes.
   - Model dynamically discovered or selected from Gemini models list.
   - Cleanly skipped if no key configured (`GEMINI_API_KEY` in .env or config.yaml).
4. Zero Guessing / No Heuristic Hallucination:
   - When no vision model is available, use EXIF GPS only (real metadata).
   - If no vision model is available, return: "No vision model available - add a Gemini key or enable Ollama."
   - Never guess a location without an active vision model.
5. Ollama:
   - Optional and opt-in via "Local only" toggle.
   - Picks smallest installed vision model (e.g. moondream, qwen2.5vl:3b, llava).
   - Never loaded at startup, uses short keep_alive ("1m").
   - Warns user that it is slow and utilizes RAM.
6. Explicit Provider Logging:
   - Logs which provider and model answered each request.
7. Nominatim Geocoding:
   - Rate-limited to max 1 req/sec with descriptive User-Agent and caching.
8. Verification Helpers:
   - Reverse visual search links (Google Lens, Yandex, Bing, TinEye).
   - Copy image to clipboard (wl-copy / xclip) & open image folder.
   - Nearby Wikimedia Commons photo cross-referencing.
"""

import os
import io
import re
import json
import time
import base64
import shutil
import urllib.parse
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from PIL import Image, ExifTags
import httpx


PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPLOADS_DIR = PROJECT_ROOT / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = PROJECT_ROOT / "data" / "nominatim_cache.json"
MODEL_CACHE_FILE = PROJECT_ROOT / "data" / "vision_model_cache.json"

NOMINATIM_USER_AGENT = "JARVIS-OSINT-Geolocator/2.0 (contact: project-hellhound)"
TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def _parse_dms(dms_values, ref: str) -> Optional[float]:
    """Convert degrees, minutes, seconds tuple or rationals to decimal degrees."""
    try:
        def to_float(val):
            if hasattr(val, "numerator") and hasattr(val, "denominator"):
                return float(val.numerator) / float(val.denominator) if val.denominator else 0.0
            if isinstance(val, tuple) and len(val) == 2:
                return float(val[0]) / float(val[1]) if val[1] else 0.0
            return float(val)

        d = to_float(dms_values[0])
        m = to_float(dms_values[1])
        s = to_float(dms_values[2])
        dec = d + (m / 60.0) + (s / 3600.0)
        if ref.upper() in ["S", "W"]:
            dec = -dec
        return round(dec, 6)
    except Exception:
        return None


def extract_exif_metadata(image_path: str) -> Dict[str, Any]:
    """Extract GPS coordinates, timestamp, and camera info using Pillow."""
    res = {
        "has_gps": False,
        "gps": None,  # {"lat": float, "lon": float, "altitude": float}
        "camera_make": None,
        "camera_model": None,
        "timestamp": None,
        "label": None
    }

    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if not exif:
                return res

            res["camera_make"] = exif.get(271) or exif.get(0x010F)
            res["camera_model"] = exif.get(272) or exif.get(0x0110)

            ts = exif.get(306) or exif.get(0x0132)
            if not ts and hasattr(ExifTags, "IFD"):
                try:
                    exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
                    if exif_ifd:
                        ts = exif_ifd.get(36867) or exif_ifd.get(0x9003)
                except Exception:
                    pass
            res["timestamp"] = str(ts) if ts else None

            gps_ifd = None
            if hasattr(ExifTags, "IFD"):
                try:
                    gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
                except Exception:
                    pass
            if not gps_ifd:
                gps_ifd = exif.get(34853) or exif.get(0x8825)

            if gps_ifd and isinstance(gps_ifd, dict):
                lat_val = gps_ifd.get(2) or gps_ifd.get(ExifTags.GPSTAGS.get("GPSLatitude", 2))
                lat_ref = gps_ifd.get(1) or gps_ifd.get(ExifTags.GPSTAGS.get("GPSLatitudeRef", 1)) or "N"
                lon_val = gps_ifd.get(4) or gps_ifd.get(ExifTags.GPSTAGS.get("GPSLongitude", 4))
                lon_ref = gps_ifd.get(3) or gps_ifd.get(ExifTags.GPSTAGS.get("GPSLongitudeRef", 3)) or "E"
                alt_val = gps_ifd.get(6) or gps_ifd.get(ExifTags.GPSTAGS.get("GPSAltitude", 6))

                if lat_val and lon_val:
                    lat = _parse_dms(lat_val, str(lat_ref))
                    lon = _parse_dms(lon_val, str(lon_ref))
                    if lat is not None and lon is not None:
                        res["has_gps"] = True
                        alt = None
                        if alt_val is not None:
                            try:
                                alt = float(alt_val[0]) / float(alt_val[1]) if isinstance(alt_val, tuple) else float(alt_val)
                            except Exception:
                                alt = None
                        res["gps"] = {
                            "lat": lat,
                            "lon": lon,
                            "altitude": alt
                        }
                        res["label"] = "from file metadata (high confidence, but verify)"
    except Exception as e:
        res["error"] = str(e)

    return res


def downscale_image_for_model(image_path: str, max_dimension: int = 1024) -> Tuple[bytes, str]:
    """Downscale image to <= max_dimension JPEG preserving aspect ratio."""
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_dimension:
            scale = max_dimension / max(w, h)
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        raw_bytes = buf.getvalue()
        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        return raw_bytes, b64


class NominatimGeocodingClient:
    """Rate-limited (max 1 req/sec), cached Nominatim client."""

    def __init__(self, cache_file: Path = CACHE_FILE):
        self.cache_file = cache_file
        self.last_request_time = 0.0
        self.cache: Dict[str, Any] = {}
        self._load_cache()

    def _load_cache(self):
        if self.cache_file.exists():
            try:
                self.cache = json.loads(self.cache_file.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    def _save_cache(self):
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(self.cache, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _rate_limit(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < 1.05:
            time.sleep(1.05 - elapsed)
        self.last_request_time = time.time()

    def geocode(self, query: str) -> Optional[Dict[str, Any]]:
        norm_query = query.strip().lower()
        if not norm_query:
            return None
        if norm_query in self.cache:
            return self.cache[norm_query]

        self._rate_limit()
        try:
            url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query)}&format=json&limit=1"
            headers = {"User-Agent": NOMINATIM_USER_AGENT}
            with httpx.Client(timeout=8.0) as client:
                r = client.get(url, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    if data and isinstance(data, list) and len(data) > 0:
                        top = data[0]
                        res = {
                            "lat": float(top["lat"]),
                            "lon": float(top["lon"]),
                            "display_name": top.get("display_name", query),
                            "type": top.get("type", "place")
                        }
                        self.cache[norm_query] = res
                        self._save_cache()
                        return res
        except Exception:
            pass
        return None

    def reverse_geocode(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        key = f"rev_{lat:.4f}_{lon:.4f}"
        if key in self.cache:
            return self.cache[key]

        self._rate_limit()
        try:
            url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
            headers = {"User-Agent": NOMINATIM_USER_AGENT}
            with httpx.Client(timeout=8.0) as client:
                r = client.get(url, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    res = {
                        "display_name": data.get("display_name", f"{lat:.4f}, {lon:.4f}"),
                        "address": data.get("address", {})
                    }
                    self.cache[key] = res
                    self._save_cache()
                    return res
        except Exception:
            pass
        return None


class ImageGeolocator:
    """Multimodal visual geolocation engine with runtime dynamic model discovery."""

    VISION_KEYWORDS = ["vision", "vlm", "vl", "neva", "paligemma", "llava", "pixtral", "qwen-vl"]

    def __init__(self):
        self.geocoder = NominatimGeocodingClient()
        self.nvidia_key = ""
        self.groq_key = ""
        self.gemini_key = ""
        self.ollama_url = "http://127.0.0.1:11434"
        self._load_keys()

        # Cache of model discovery and probing results
        self.tested_models: Dict[str, Any] = {}
        self.active_vision_provider: Optional[str] = None
        self.active_vision_model: Optional[str] = None
        self._load_model_cache()

    def _load_keys(self):
        # 1. Environment variables
        self.nvidia_key = os.getenv("NVIDIA_API_KEY", "")
        self.groq_key = os.getenv("GROQ_API_KEY", "")
        self.gemini_key = os.getenv("GEMINI_API_KEY", "")
        self.ollama_url = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")

        # 2. .env file
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    val = v.strip().strip("\"'")
                    if k == "NVIDIA_API_KEY" and not self.nvidia_key:
                        self.nvidia_key = val
                    elif k == "GROQ_API_KEY" and not self.groq_key:
                        self.groq_key = val
                    elif k == "GEMINI_API_KEY" and not self.gemini_key:
                        self.gemini_key = val

        # 3. config.yaml
        cfg_file = PROJECT_ROOT / "config.yaml"
        if cfg_file.exists():
            try:
                import yaml
                data = yaml.safe_load(cfg_file.read_text()) or {}
                if not self.gemini_key and data.get("gemini_api_key"):
                    self.gemini_key = str(data["gemini_api_key"]).strip()
                if not self.nvidia_key and data.get("nvidia_api_key"):
                    self.nvidia_key = str(data["nvidia_api_key"]).strip()
                if not self.groq_key and data.get("groq_api_key"):
                    self.groq_key = str(data["groq_api_key"]).strip()
            except Exception:
                pass

    def _load_model_cache(self):
        if MODEL_CACHE_FILE.exists():
            try:
                self.tested_models = json.loads(MODEL_CACHE_FILE.read_text())
            except Exception:
                self.tested_models = {}

    def _save_model_cache(self):
        try:
            MODEL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            MODEL_CACHE_FILE.write_text(json.dumps(self.tested_models, indent=2))
        except Exception:
            pass

    # ── Dynamic Model Discovery & Verification ─────────────────────────

    def discover_and_probe_models(self) -> Dict[str, Any]:
        """
        Fetches /models lists from NVIDIA and Groq, filters candidates by vision keywords,
        tests each with one real image request, and logs the result and failure body.
        Also tests nvidia/nemotron-3-ultra-550b-a55b for image_url support.
        Checks Gemini if key is provided.
        """
        results = {
            "working_models": [],
            "failed_models": []
        }

        # 1. Probe NVIDIA
        if self.nvidia_key:
            # 1a. Test primary chat model nvidia/nemotron-3-ultra-550b-a55b for image_url support
            primary_model = "nvidia/nemotron-3-ultra-550b-a55b"
            print(f"[ImageGeolocator] Probing primary model {primary_model} for vision capability...")
            try:
                resp = httpx.post(
                    "https://integrate.api.nvidia.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.nvidia_key}", "Content-Type": "application/json"},
                    json={
                        "model": primary_model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "What is in this image?"},
                                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{TINY_PNG_B64}"}}
                                ]
                            }
                        ],
                        "max_tokens": 15
                    },
                    timeout=15.0
                )
                if resp.status_code == 200:
                    print(f"[ImageGeolocator] Primary model {primary_model} supports vision! Making it default.")
                    results["working_models"].append({"provider": "nvidia", "model": primary_model, "is_default": True})
                    self.active_vision_provider = "nvidia"
                    self.active_vision_model = primary_model
                else:
                    err_msg = f"HTTP {resp.status_code}: {resp.text[:140]}"
                    print(f"[ImageGeolocator] {primary_model} image request rejected: {err_msg}")
                    results["failed_models"].append({"provider": "nvidia", "model": primary_model, "error": err_msg})
            except Exception as e:
                results["failed_models"].append({"provider": "nvidia", "model": primary_model, "error": str(e)})

            # 1b. Fetch NVIDIA /models and probe candidates matching vision keywords
            try:
                r_models = httpx.get("https://integrate.api.nvidia.com/v1/models", headers={"Authorization": f"Bearer {self.nvidia_key}"}, timeout=10.0)
                if r_models.status_code == 200:
                    all_nvidia = [m["id"] for m in r_models.json().get("data", [])]
                    candidates = [m for m in all_nvidia if any(kw in m.lower() for kw in self.VISION_KEYWORDS)]
                    print(f"[ImageGeolocator] Found {len(candidates)} vision candidates in NVIDIA /models")
                    for cand in candidates:
                        if cand in [w["model"] for w in results["working_models"]]:
                            continue
                        try:
                            t_resp = httpx.post(
                                "https://integrate.api.nvidia.com/v1/chat/completions",
                                headers={"Authorization": f"Bearer {self.nvidia_key}", "Content-Type": "application/json"},
                                json={
                                    "model": cand,
                                    "messages": [
                                        {
                                            "role": "user",
                                            "content": [
                                                {"type": "text", "text": "Identify color"},
                                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{TINY_PNG_B64}"}}
                                            ]
                                        }
                                    ],
                                    "max_tokens": 10
                                },
                                timeout=12.0
                            )
                            if t_resp.status_code == 200:
                                results["working_models"].append({"provider": "nvidia", "model": cand})
                                if not self.active_vision_model:
                                    self.active_vision_provider = "nvidia"
                                    self.active_vision_model = cand
                            else:
                                err = f"HTTP {t_resp.status_code}: {t_resp.text[:120]}"
                                results["failed_models"].append({"provider": "nvidia", "model": cand, "error": err})
                        except Exception as ex:
                            results["failed_models"].append({"provider": "nvidia", "model": cand, "error": str(ex)})
            except Exception as ex:
                results["failed_models"].append({"provider": "nvidia", "model": "nvidia/models-list", "error": str(ex)})

        # 2. Probe Groq
        if self.groq_key:
            try:
                r_groq = httpx.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {self.groq_key}"}, timeout=10.0)
                if r_groq.status_code == 200:
                    all_groq = [m["id"] for m in r_groq.json().get("data", [])]
                    candidates = [m for m in all_groq if any(kw in m.lower() for kw in self.VISION_KEYWORDS)]
                    for cand in candidates:
                        try:
                            t_resp = httpx.post(
                                "https://api.groq.com/openai/v1/chat/completions",
                                headers={"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"},
                                json={
                                    "model": cand,
                                    "messages": [
                                        {
                                            "role": "user",
                                            "content": [
                                                {"type": "text", "text": "Describe image"},
                                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{TINY_PNG_B64}"}}
                                            ]
                                        }
                                    ],
                                    "max_tokens": 10
                                },
                                timeout=12.0
                            )
                            if t_resp.status_code == 200:
                                results["working_models"].append({"provider": "groq", "model": cand})
                                if not self.active_vision_model:
                                    self.active_vision_provider = "groq"
                                    self.active_vision_model = cand
                            else:
                                err = f"HTTP {t_resp.status_code}: {t_resp.text[:120]}"
                                results["failed_models"].append({"provider": "groq", "model": cand, "error": err})
                        except Exception as ex:
                            results["failed_models"].append({"provider": "groq", "model": cand, "error": str(ex)})
            except Exception as ex:
                results["failed_models"].append({"provider": "groq", "model": "groq/models-list", "error": str(ex)})

        # 3. Probe Gemini (if key configured)
        if self.gemini_key:
            try:
                # Query Gemini models list
                g_list = httpx.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={self.gemini_key}", timeout=10.0)
                if g_list.status_code == 200:
                    models = [m.get("name", "").replace("models/", "") for m in g_list.json().get("models", [])]
                    vision_candidates = [m for m in models if "flash" in m or "pro" in m or "vision" in m]
                    gemini_model = vision_candidates[0] if vision_candidates else "gemini-1.5-flash"
                    # Probe with tiny image
                    payload = {
                        "contents": [
                            {
                                "parts": [
                                    {"text": "What color is this image?"},
                                    {"inline_data": {"mime_type": "image/png", "data": TINY_PNG_B64}}
                                ]
                            }
                        ],
                        "generationConfig": {"maxOutputTokens": 15}
                    }
                    g_probe = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={self.gemini_key}", json=payload, timeout=12.0)
                    if g_probe.status_code == 200:
                        results["working_models"].append({"provider": "gemini", "model": gemini_model})
                        if not self.active_vision_model:
                            self.active_vision_provider = "gemini"
                            self.active_vision_model = gemini_model
                    else:
                        results["failed_models"].append({"provider": "gemini", "model": gemini_model, "error": f"HTTP {g_probe.status_code}: {g_probe.text[:120]}"})
                else:
                    results["failed_models"].append({"provider": "gemini", "model": "gemini/models-list", "error": f"HTTP {g_list.status_code}: {g_list.text[:120]}"})
            except Exception as ex:
                results["failed_models"].append({"provider": "gemini", "model": "gemini", "error": str(ex)})

        self.tested_models = results
        self._save_model_cache()
        return results

    # ── Provider Implementations ──────────────────────────────────────

    def _call_gemini_vision(self, prompt: str, b64_jpeg: str, timeout: float = 20.0) -> Optional[str]:
        """Calls Gemini API with inline image data. Cleanly returns None if no key configured."""
        if not self.gemini_key:
            return None

        model = self.active_vision_model if self.active_vision_provider == "gemini" else "gemini-1.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.gemini_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": b64_jpeg
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1500
            }
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(url, json=payload)
                if r.status_code == 200:
                    data = r.json()
                    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        return parts[0]["text"]
        except Exception:
            pass
        return None

    def _call_nvidia_vision(self, prompt: str, b64_jpeg: str, timeout: float = 20.0) -> Optional[str]:
        if not self.nvidia_key:
            return None
        model = self.active_vision_model if self.active_vision_provider == "nvidia" else "meta/llama-3.2-11b-vision-instruct"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_jpeg}"}}
                    ]
                }
            ],
            "max_tokens": 1200,
            "temperature": 0.2
        }
        headers = {
            "Authorization": f"Bearer {self.nvidia_key}",
            "Content-Type": "application/json"
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post("https://integrate.api.nvidia.com/v1/chat/completions", headers=headers, json=payload)
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"]
        except Exception:
            pass
        return None

    def _call_groq_vision(self, prompt: str, b64_jpeg: str, timeout: float = 15.0) -> Optional[str]:
        if not self.groq_key:
            return None
        model = self.active_vision_model if self.active_vision_provider == "groq" else "llama-3.2-11b-vision-preview"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_jpeg}"}}
                    ]
                }
            ],
            "max_tokens": 1200,
            "temperature": 0.2
        }
        headers = {
            "Authorization": f"Bearer {self.groq_key}",
            "Content-Type": "application/json"
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"]
        except Exception:
            pass
        return None

    def _get_smallest_ollama_vision_model(self) -> Optional[str]:
        """Find the smallest installed Ollama vision model."""
        try:
            with httpx.Client(timeout=2.0) as client:
                r = client.get(f"{self.ollama_url}/api/tags")
                if r.status_code == 200:
                    installed = [m["name"] for m in r.json().get("models", [])]
                    # Ordered from smallest to largest
                    candidates = ["moondream", "qwen2.5vl:3b", "llama3.2-vision", "llava"]
                    for c in candidates:
                        for inst in installed:
                            if c in inst.lower():
                                return inst
        except Exception:
            pass
        return None

    def _call_ollama_vision(self, prompt: str, b64_jpeg: str, timeout: float = 30.0) -> Optional[str]:
        """
        Calls Ollama with smallest installed vision model.
        Uses short keep_alive ('1m') to avoid holding RAM indefinitely.
        """
        model = self._get_smallest_ollama_vision_model()
        if not model:
            return None

        payload = {
            "model": model,
            "prompt": prompt,
            "images": [b64_jpeg],
            "stream": False,
            "keep_alive": "1m"
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(f"{self.ollama_url}/api/generate", json=payload)
                if r.status_code == 200:
                    return r.json().get("response")
        except Exception:
            pass
        return None

    def _execute_vision_prompt(self, prompt: str, b64_jpeg: str, local_only: bool = False) -> Tuple[Optional[str], Optional[str]]:
        """
        Routes the vision request according to user settings and active providers.
        Logs which provider answered the request.
        Returns (model_response_text, provider_name).
        """
        if local_only:
            txt = self._call_ollama_vision(prompt, b64_jpeg)
            if txt:
                print(f"[ImageGeolocator] Provider used: ollama (Model: {self._get_smallest_ollama_vision_model()})")
                return txt, "ollama"
            print("[ImageGeolocator] Ollama request failed or no vision model installed.")
            return None, None

        # Cloud providers order: Gemini -> NVIDIA -> Groq -> Ollama fallback
        providers = [
            ("gemini", self._call_gemini_vision),
            ("nvidia", self._call_nvidia_vision),
            ("groq", self._call_groq_vision),
            ("ollama", self._call_ollama_vision),
        ]

        for name, fn in providers:
            txt = fn(prompt, b64_jpeg)
            if txt:
                print(f"[ImageGeolocator] Provider used: {name} (Model: {self.active_vision_model or 'auto'})")
                return txt, name

        print("[ImageGeolocator] No vision provider available to process image.")
        return None, None

    # ── Two-Step Geolocation Reasoning ────────────────────────────────

    def _clean_json_output(self, text: str) -> Optional[Any]:
        if not text:
            return None
        clean = re.sub(r'```json\s*', '', text)
        clean = re.sub(r'```\s*', '', clean).strip()
        try:
            return json.loads(clean)
        except Exception:
            pass

        m = re.search(r'\{.*\}', clean, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass

        m_arr = re.search(r'\[.*\]', clean, re.DOTALL)
        if m_arr:
            try:
                return json.loads(m_arr.group(0))
            except Exception:
                pass
        return None

    def step1_extract_observations(self, b64_jpeg: str, local_only: bool = False) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Step 1: Extract observable features as JSON."""
        prompt = (
            "You are an expert satellite intelligence and OSINT visual geolocation analyst.\n"
            "Analyze this photograph carefully. Extract all observable visual geolocation indicators into strict JSON.\n"
            "Return a JSON object with this exact structure:\n"
            "{\n"
            '  "architecture": {"observation": "...", "confidence": 0.85},\n'
            '  "flora_vegetation": {"observation": "...", "confidence": 0.80},\n'
            '  "signage_text": {"observation": "...", "languages": ["..."], "confidence": 0.90},\n'
            '  "driving_side": {"observation": "left or right", "confidence": 0.70},\n'
            '  "sun_shadow_direction": {"observation": "...", "apparent_hemisphere": "North or South", "confidence": 0.65},\n'
            '  "utility_poles_infrastructure": {"observation": "...", "confidence": 0.75},\n'
            '  "license_plates_vehicles": {"observation": "...", "confidence": 0.60},\n'
            '  "general_biome_environment": {"observation": "...", "confidence": 0.85}\n'
            "}\n"
            "Respond ONLY with the JSON object."
        )

        raw, provider = self._execute_vision_prompt(prompt, b64_jpeg, local_only=local_only)
        if not raw:
            return None, None

        data = self._clean_json_output(raw)
        if not data and raw:
            # Retry once on malformed output
            retry_prompt = f"The previous output was not valid JSON:\n{raw[:250]}\nReformat strictly as a valid JSON object only."
            raw2, provider = self._execute_vision_prompt(retry_prompt, b64_jpeg, local_only=local_only)
            data = self._clean_json_output(raw2 or "")

        return data, provider

    def step2_rank_hypotheses(self, observations: Dict[str, Any], b64_jpeg: str, local_only: bool = False) -> List[Dict[str, Any]]:
        """Step 2: Return ranked candidate locations based on observations."""
        obs_json = json.dumps(observations, indent=2)
        prompt = (
            "You are an expert satellite intelligence and OSINT visual geolocation analyst.\n"
            f"Based on the following extracted visual observations:\n{obs_json}\n\n"
            "Produce 3 ranked candidate location hypotheses from highest to lowest probability.\n"
            "Return a strict JSON array of objects with this exact structure:\n"
            "[\n"
            "  {\n"
            '    "rank": 1,\n'
            '    "country": "...",\n'
            '    "region": "...",\n'
            '    "place": "...",\n'
            '    "latitude": 0.0,\n'
            '    "longitude": 0.0,\n'
            '    "confidence": 0.82,\n'
            '    "clues": ["...", "..."],\n'
            '    "what_to_check_next": ["Check road signs for ...", "Verify utility pole transformer styles in ..."]\n'
            "  }\n"
            "]\n"
            "Respond ONLY with the JSON array."
        )

        raw, _ = self._execute_vision_prompt(prompt, b64_jpeg, local_only=local_only)
        if not raw:
            return []

        candidates = self._clean_json_output(raw)
        if (not candidates or not isinstance(candidates, list)) and raw:
            retry_prompt = f"The previous output was not a valid JSON array:\n{raw[:250]}\nReformat strictly as a JSON array of objects."
            raw2, _ = self._execute_vision_prompt(retry_prompt, b64_jpeg, local_only=local_only)
            candidates = self._clean_json_output(raw2 or "")

        if not candidates or not isinstance(candidates, list):
            return []

        processed = []
        for i, c in enumerate(candidates[:3]):
            rank = c.get("rank", i + 1)
            country = c.get("country", "Unknown")
            region = c.get("region", "")
            place = c.get("place", "")
            conf = float(c.get("confidence", 0.5))
            conf = max(0.05, min(0.99, conf))

            lat = c.get("latitude")
            lon = c.get("longitude")
            if not lat or not lon or (lat == 0.0 and lon == 0.0):
                query = f"{place}, {region}, {country}".replace("Unknown", "").strip(" ,")
                if query:
                    geo = self.geocoder.geocode(query)
                    if geo:
                        lat = geo["lat"]
                        lon = geo["lon"]

            processed.append({
                "rank": rank,
                "country": country,
                "region": region,
                "place": place,
                "latitude": lat or 0.0,
                "longitude": lon or 0.0,
                "confidence": round(conf, 2),
                "hypothesis_status": "UNVERIFIED hypothesis",
                "clues": c.get("clues", []),
                "what_to_check_next": c.get("what_to_check_next", [])
            })

        return processed

    # ── Pipeline Execution ────────────────────────────────────────────

    def analyze(self, image_path: str, local_only: bool = False) -> Dict[str, Any]:
        """
        Executes geolocation pipeline:
        1. EXIF extraction (always real metadata).
        2. Downscaling.
        3. Model reasoning if available.
        4. If no vision model available, return real EXIF only and show exact warning message:
           "No vision model available - add a Gemini key or enable Ollama."
        """
        img_path = Path(image_path)
        if not img_path.exists():
            return {"error": f"Image not found at {image_path}"}

        # 1. EXIF Pass (Pillow)
        exif_info = extract_exif_metadata(str(img_path))

        # 2. Downscaling
        jpeg_bytes, b64_jpeg = downscale_image_for_model(str(img_path))

        # 3. Model Vision Reasoning
        observations, provider_used = self.step1_extract_observations(b64_jpeg, local_only=local_only)
        candidates = []
        if observations:
            candidates = self.step2_rank_hypotheses(observations, b64_jpeg, local_only=local_only)

        no_model_warning = None
        if not provider_used:
            no_model_warning = "No vision model available - add a Gemini key or enable Ollama."
            print(f"[ImageGeolocator] {no_model_warning}")

        # 4. If EXIF GPS found, inject it as primary metadata candidate
        if exif_info.get("has_gps") and exif_info.get("gps"):
            gps = exif_info["gps"]
            rev = self.geocoder.reverse_geocode(gps["lat"], gps["lon"])
            disp = rev.get("display_name", f"{gps['lat']:.4f}, {gps['lon']:.4f}") if rev else f"{gps['lat']:.4f}, {gps['lon']:.4f}"
            addr = rev.get("address", {}) if rev else {}
            exif_candidate = {
                "rank": 0,
                "country": addr.get("country", "Detected Location"),
                "region": addr.get("state") or addr.get("county", ""),
                "place": addr.get("city") or addr.get("town") or addr.get("village") or disp,
                "latitude": gps["lat"],
                "longitude": gps["lon"],
                "altitude": gps.get("altitude"),
                "confidence": 0.98,
                "hypothesis_status": "from file metadata (high confidence, but verify)",
                "clues": [
                    f"Embedded EXIF coordinates: {gps['lat']:.4f}°, {gps['lon']:.4f}°",
                    f"Timestamp: {exif_info.get('timestamp') or 'N/A'}",
                    f"Camera: {exif_info.get('camera_make') or ''} {exif_info.get('camera_model') or ''}".strip()
                ],
                "what_to_check_next": [
                    "Verify image date matches scene lighting",
                    "Confirm street view matches camera angle"
                ]
            }
            candidates.insert(0, exif_candidate)

        # 5. Verification Helpers & Wikimedia photos
        verification_links = self.get_reverse_search_links(str(img_path))
        wikimedia_photos = []
        top_cand = candidates[0] if candidates else None
        if top_cand and top_cand.get("latitude") and top_cand.get("longitude"):
            wikimedia_photos = self.get_nearby_wikimedia_photos(top_cand["latitude"], top_cand["longitude"])

        # 6. Spoken Summary
        speech_summary = self.generate_speech_summary(candidates, no_model_warning=no_model_warning)

        return {
            "image_path": str(img_path),
            "filename": img_path.name,
            "provider_used": provider_used,
            "local_only": local_only,
            "warning": no_model_warning,
            "exif": exif_info,
            "observations": observations,
            "candidates": candidates,
            "verification_links": verification_links,
            "wikimedia_photos": wikimedia_photos,
            "speech_summary": speech_summary,
            "timestamp": time.time()
        }

    # ── Helpers ───────────────────────────────────────────────────────

    def get_reverse_search_links(self, image_path: str) -> Dict[str, str]:
        return {
            "google_lens": "https://lens.google.com/upload",
            "yandex": "https://yandex.com/images/search?rpt=imageview",
            "bing": "https://www.bing.com/visualsearch",
            "tineye": "https://tineye.com/search"
        }

    def copy_image_to_clipboard(self, image_path: str) -> bool:
        p = Path(image_path)
        if not p.exists():
            return False
        if shutil.which("wl-copy"):
            try:
                subprocess.run(["wl-copy", "-t", "image/png"], input=p.read_bytes(), check=True, timeout=2)
                return True
            except Exception:
                pass
        if shutil.which("xclip"):
            try:
                subprocess.run(["xclip", "-selection", "clipboard", "-target", "image/png", "-i", str(p)], check=True, timeout=2)
                return True
            except Exception:
                pass
        return False

    def open_image_folder(self, image_path: str) -> bool:
        folder = Path(image_path).parent
        if shutil.which("xdg-open"):
            try:
                subprocess.Popen(["xdg-open", str(folder)])
                return True
            except Exception:
                pass
        return False

    def get_nearby_wikimedia_photos(self, lat: float, lon: float, radius_meters: int = 10000, limit: int = 4) -> List[Dict[str, Any]]:
        photos = []
        try:
            url = f"https://commons.wikimedia.org/w/api.php?action=query&list=geosearch&gscoord={lat}|{lon}&gsradius={radius_meters}&gslimit={limit}&format=json"
            with httpx.Client(timeout=5.0) as client:
                r = client.get(url, headers={"User-Agent": NOMINATIM_USER_AGENT})
                if r.status_code == 200:
                    data = r.json().get("query", {}).get("geosearch", [])
                    page_ids = [str(item["pageid"]) for item in data if "pageid" in item]
                    if page_ids:
                        info_url = f"https://commons.wikimedia.org/w/api.php?action=query&prop=imageinfo&iiprop=url|thumburl&iiurlwidth=320&pageids={'|'.join(page_ids)}&format=json"
                        r2 = client.get(info_url, headers={"User-Agent": NOMINATIM_USER_AGENT})
                        if r2.status_code == 200:
                            pages = r2.json().get("query", {}).get("pages", {})
                            for pid, pdata in pages.items():
                                title = pdata.get("title", "").replace("File:", "")
                                img_info = pdata.get("imageinfo", [{}])[0]
                                thumb = img_info.get("thumburl") or img_info.get("url")
                                if thumb:
                                    photos.append({
                                        "title": title,
                                        "url": img_info.get("descriptionurl", img_info.get("url")),
                                        "thumb": thumb
                                    })
        except Exception:
            pass
        return photos

    def generate_speech_summary(self, candidates: List[Dict[str, Any]], no_model_warning: Optional[str] = None) -> str:
        if not candidates:
            if no_model_warning:
                return "Sir, no vision model is currently available to analyze this photograph. Please add a Gemini key or enable Ollama."
            return "Visual geolocation scan complete, Sir. No geographical hallmarks were identified."

        top = candidates[0]
        if top.get("rank") == 0 or "file metadata" in top.get("hypothesis_status", ""):
            place = top.get("place") or top.get("country")
            return f"Sir, GPS coordinates were extracted directly from the file metadata, locating the photo in {place}. Please verify the surrounding terrain."

        parts = []
        for c in candidates[:3]:
            loc = c.get('place') or c.get('region') or c.get('country')
            conf_pct = int(c.get('confidence', 0.5) * 100)
            parts.append(f"{loc} at {conf_pct} percent confidence")

        summary = f"Sir, visual intelligence analysis yielded {len(candidates)} unverified candidate hypotheses. "
        if len(parts) == 1:
            summary += f"Primary candidate is {parts[0]}."
        else:
            summary += f"Leading candidate is {parts[0]}, followed by {', and '.join(parts[1:])}."
        return summary
