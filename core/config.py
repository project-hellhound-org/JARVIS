# core/config.py
"""
Unified configuration loader for J.A.R.V.I.S.
Enforces hierarchical resolution priority:
  1. Process Environment Variables (os.environ)
  2. Project Root .env File
  3. Project Root config.yaml File
  4. Safe Empty Default ("")
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

# Canonical definition of the 12 core configuration keys and their YAML / alias mappings
CONFIG_KEY_SPECS: Dict[str, Dict[str, Any]] = {
    "NVIDIA_API_KEY": {
        "yaml_keys": ["nvidia_api_key"],
        "aliases": [],
        "default": "",
    },
    "GROQ_API_KEY": {
        "yaml_keys": ["groq_api_key"],
        "aliases": [],
        "default": "",
    },
    "GEMINI_API_KEY": {
        "yaml_keys": ["gemini_api_key"],
        "aliases": [],
        "default": "",
    },
    "CESIUM_ION_TOKEN": {
        "yaml_keys": ["cesium_ion_token"],
        "aliases": [],
        "default": "",
    },
    "NASA_FIRMS_MAP_KEY": {
        "yaml_keys": ["nasa_firms_key", "nasa_firms_map_key", "firms_map_key"],
        "aliases": ["FIRMS_MAP_KEY"],
        "default": "",
    },
    "FISH_AUDIO_API_KEY": {
        "yaml_keys": ["fish_audio_api_key"],
        "aliases": [],
        "default": "",
    },
    "FISH_AUDIO_VOICE_ID": {
        "yaml_keys": ["fish_audio_voice_id"],
        "aliases": [],
        "default": "05b36da8574341d0803391491850db20",
    },
    "AISSTREAM_API_KEY": {
        "yaml_keys": ["aisstream_api_key"],
        "aliases": [],
        "default": "",
    },
    "YOUTUBE_API_KEY": {
        "yaml_keys": ["youtube_api_key"],
        "aliases": [],
        "default": "",
    },
    "GITHUB_TOKEN": {
        "yaml_keys": ["github_token"],
        "aliases": [],
        "default": "",
    },
    "HASS_URL": {
        "yaml_keys": ["hass_url"],
        "aliases": [],
        "default": "",
    },
    "HASS_TOKEN": {
        "yaml_keys": ["hass_token"],
        "aliases": [],
        "default": "",
    },
    "RAPIDAPI_KEY": {
        "yaml_keys": ["rapidapi_key"],
        "aliases": [],
        "default": "",
    },
}


def is_placeholder(val: Any) -> bool:
    """Check if a string represents an unpopulated placeholder."""
    if not isinstance(val, str):
        return False
    v = val.strip()
    if not v:
        return True
    v_upper = v.upper()
    if v_upper.startswith("YOUR_") or v_upper.endswith("_HERE"):
        return True
    if v.startswith("<") and v.endswith(">"):
        return True
    if v.lower() in ("your-key-here", "your_key_here"):
        return True
    return False


def is_key_configured(name: str) -> bool:
    """
    Check whether a key is configured with a non-empty, non-placeholder value
    in process environment, .env file, or config.yaml.
    Never exposes the configured secret value.
    """
    spec = CONFIG_KEY_SPECS.get(name.upper(), {})
    env_candidates = [name, name.upper()] + spec.get("aliases", [])
    for ek in env_candidates:
        if ek in os.environ and not is_placeholder(os.environ[ek]):
            return True

    env_file_data = _read_env_file()
    for ek in env_candidates:
        if ek in env_file_data and not is_placeholder(env_file_data[ek]):
            return True

    yaml_candidates = [name, name.lower()] + spec.get("yaml_keys", [])
    yaml_data = _read_yaml_config()
    for yk in yaml_candidates:
        if yk in yaml_data:
            val = yaml_data[yk]
            if val is not None and not is_placeholder(val):
                return True

    return False


def _read_env_file() -> Dict[str, str]:
    """Parse .env file into key-value pairs without modifying os.environ."""
    env_vars: Dict[str, str] = {}
    if not ENV_PATH.exists():
        return env_vars
    try:
        content = ENV_PATH.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip("\"'")
            if k:
                env_vars[k] = v
    except Exception:
        pass
    return env_vars


def _read_yaml_config() -> Dict[str, Any]:
    """Parse config.yaml safely."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def get_config(name: str, default: Any = "") -> Any:
    """
    Retrieve configuration value following strict priority:
      1. os.environ
      2. .env file
      3. config.yaml
      4. default value
    """
    name_upper = name.upper()
    name_lower = name.lower()

    spec = CONFIG_KEY_SPECS.get(name_upper)
    env_candidates: List[str] = [name, name_upper]
    yaml_candidates: List[str] = [name, name_lower]

    if spec:
        env_candidates.extend(spec.get("aliases", []))
        yaml_candidates.extend(spec.get("yaml_keys", []))
    elif name_upper == "FIRMS_MAP_KEY":
        env_candidates.append("NASA_FIRMS_MAP_KEY")
        yaml_candidates.extend(["nasa_firms_key", "nasa_firms_map_key", "firms_map_key"])

    # Deduplicate preserving order
    seen_env = set()
    ordered_env = []
    for ek in env_candidates:
        if ek not in seen_env:
            seen_env.add(ek)
            ordered_env.append(ek)

    seen_yaml = set()
    ordered_yaml = []
    for yk in yaml_candidates:
        if yk not in seen_yaml:
            seen_yaml.add(yk)
            ordered_yaml.append(yk)

    # 1. Check os.environ
    for ek in ordered_env:
        if ek in os.environ:
            val = os.environ[ek]
            if not is_placeholder(val):
                return val

    # 2. Check .env file
    env_file_data = _read_env_file()
    for ek in ordered_env:
        if ek in env_file_data:
            val = env_file_data[ek]
            if not is_placeholder(val):
                return val

    # 3. Check config.yaml
    yaml_data = _read_yaml_config()
    for yk in ordered_yaml:
        if yk in yaml_data:
            val = yaml_data[yk]
            if isinstance(val, str):
                if not is_placeholder(val):
                    return val
            elif val is not None:
                return val

    # 4. Fallback to default
    if default != "":
        return default
    if spec:
        return spec.get("default", "")
    return default


def load_all_config() -> Dict[str, Any]:
    """Return dictionary of all resolved configuration keys."""
    resolved: Dict[str, Any] = {}
    for key in CONFIG_KEY_SPECS:
        resolved[key] = get_config(key)
    yaml_data = _read_yaml_config()
    for k, v in yaml_data.items():
        if k not in resolved and not is_placeholder(v):
            resolved[k] = v
    return resolved


def bootstrap_environment() -> None:
    """
    Bootstrap process environment from .env and config.yaml.
    Does not overwrite existing variables already in os.environ.
    Sets necessary system flags (Fontconfig, QtWebEngine).
    """
    # 1. Load from .env file into os.environ
    env_file_data = _read_env_file()
    for k, v in env_file_data.items():
        if k not in os.environ and not is_placeholder(v):
            os.environ[k] = v

    # 2. Load from config.yaml for supported keys
    yaml_data = _read_yaml_config()
    for env_key, spec in CONFIG_KEY_SPECS.items():
        all_env_keys = [env_key] + spec.get("aliases", [])
        already_val = None
        for ek in all_env_keys:
            if ek in os.environ and not is_placeholder(os.environ[ek]):
                already_val = os.environ[ek]
                break

        if already_val is not None:
            for ek in all_env_keys:
                if ek not in os.environ:
                    os.environ[ek] = already_val
            continue

        for yk in spec.get("yaml_keys", []):
            if yk in yaml_data:
                val = yaml_data[yk]
                if val is not None and not is_placeholder(val):
                    val_str = str(val).strip()
                    for ek in all_env_keys:
                        if ek not in os.environ:
                            os.environ[ek] = val_str
                    break

    if "nvidia_model" in yaml_data and "NVIDIA_MODEL" not in os.environ:
        val = yaml_data["nvidia_model"]
        if val and not is_placeholder(val):
            os.environ["NVIDIA_MODEL"] = str(val).strip()

    # 3. Prevent Fontconfig errors
    if "FONTCONFIG_PATH" not in os.environ:
        os.environ["FONTCONFIG_PATH"] = "/etc/fonts"
    if "FONTCONFIG_FILE" not in os.environ and os.path.exists("/etc/fonts/fonts.conf"):
        os.environ["FONTCONFIG_FILE"] = "/etc/fonts/fonts.conf"

    # 4. QtWebEngine flags for audio autoplay
    current_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    required_flags = "--autoplay-policy=no-user-gesture-required --no-sandbox"
    for f in required_flags.split():
        if f not in current_flags:
            current_flags = f"{current_flags} {f}".strip()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = current_flags
