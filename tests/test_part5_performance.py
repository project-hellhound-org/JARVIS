"""
Tests for Part 5: Speed and RAM Optimizations.
Verifies Whisper idle unloading, Ollama keep_alive settings, Cesium and render loop optimizations in app.html,
and flight radar entity retention pruning.
"""
import inspect
import time
from unittest.mock import MagicMock

from frontend.desktop import JarvisAPI
from modules.image_geolocator import ImageGeolocator
from narrative.jarvis_voice import JarvisVoice


def test_whisper_idle_unload_mechanism():
    """Verify JarvisAPI includes Whisper idle unload logic that clears model references and invokes gc."""
    api = JarvisAPI()
    assert hasattr(api, "_schedule_whisper_idle_unload")

    # Mock cached models on api
    api._faster_whisper_model = MagicMock()
    api._whisper_model = MagicMock()
    api._last_whisper_access = time.time() - 65.0

    api._schedule_whisper_idle_unload()
    assert hasattr(api, "_whisper_unload_timer_started")
    assert api._whisper_unload_timer_started is True


def test_ollama_keep_alive_voice():
    """Verify jarvis_voice contains keep_alive 2m in Ollama payloads."""
    src = inspect.getsource(JarvisVoice._ask_slm)
    assert "\"keep_alive\": \"2m\"" in src


def test_ollama_keep_alive_geolocator():
    """Verify image_geolocator contains keep_alive 2m."""
    src = inspect.getsource(ImageGeolocator._call_ollama_vision)
    assert "\"keep_alive\": \"2m\"" in src


def test_app_html_visibilitychange_and_render_pausing():
    """Verify frontend/app.html implements visibilitychange listener pausing Cesium, KnowledgeGraph, and Starfield."""
    with open("frontend/app.html", "r", encoding="utf-8") as f:
        content = f.read()

    assert "visibilitychange" in content
    assert "sfStop" in content
    assert "sfStart" in content
    assert "useDefaultRenderLoop = false" in content
    assert "window.JarvisKnowledgeGraph.pause()" in content


def test_app_html_cesium_performance_settings():
    """Verify frontend/app.html configures Cesium performance governors."""
    with open("frontend/app.html", "r", encoding="utf-8") as f:
        content = f.read()

    assert "scene.maximumRenderTimeChange = 1.0;" in content
    assert "scene.globe.tileCacheSize = 50;" in content
    assert "scene.globe.maximumScreenSpaceError = 3.5;" in content
    assert "cesiumViewer.resolutionScale = Math.min(window.devicePixelRatio || 1.0, 1.25);" in content
    assert "fxaa.enabled = false" in content
    assert "fog.enabled = false" in content
    assert "shadowMap.enabled = false" in content


def test_app_html_flight_radar_pruning():
    """Verify frontend/app.html prunes flight radar contacts older than 60s and caps total contacts at 100."""
    with open("frontend/app.html", "r", encoding="utf-8") as f:
        content = f.read()

    # Verify 60s contact pruning
    assert "nowMs - val.lastSeen > 60000" in content
    # Verify retention cap of 100
    assert "_aircraftFlightMap.size > 100" in content
