import socket
import threading
import time
import json
import urllib.request
import urllib.error
import pytest
import bottle
from pathlib import Path
from frontend.desktop import setup_jarvis_bottle_routes

class DummyJarvisAPI:
    def get_adsb_flights(self, feed):
        return [{"hex": "AE1234", "callsign": "TITAN01", "type": "F35"}]
    
    def get_firms_hotspots(self):
        return [{"lat": 34.0, "lon": -118.0, "bright_ti4": 350.0}]

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

@pytest.fixture(scope="module")
def bottle_test_server():
    port = get_free_port()
    app = bottle.Bottle()
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    api = DummyJarvisAPI()
    
    setup_jarvis_bottle_routes(app, str(frontend_dir), api=api)
    
    server_thread = threading.Thread(
        target=lambda: bottle.run(app=app, host='127.0.0.1', port=port, quiet=True),
        daemon=True
    )
    server_thread.start()
    time.sleep(0.5)
    
    base_url = f"http://127.0.0.1:{port}"
    yield base_url

def test_static_app_html_serving(bottle_test_server):
    """Test that existing local assets serve 200 OK."""
    req = urllib.request.Request(f"{bottle_test_server}/")
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8", errors="ignore")
        assert "html" in content or "DOCTYPE" in content

def test_static_existing_css_asset(bottle_test_server):
    """Test that static sub-assets in frontend serve 200 OK."""
    req = urllib.request.Request(f"{bottle_test_server}/cesium/Widgets/widgets.css")
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200
        assert resp.headers.get("Content-Type", "").startswith("text/css")

def test_api_routes_serve_correctly(bottle_test_server):
    """Test that internal API endpoints operate normally."""
    req = urllib.request.Request(f"{bottle_test_server}/api/firms")
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert isinstance(data, list)
        assert data[0]["bright_ti4"] == 350.0

    req = urllib.request.Request(f"{bottle_test_server}/api/adsb/mil")
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data[0]["callsign"] == "TITAN01"

def test_strict_local_404_for_missing_file(bottle_test_server):
    """Confirm non-existent files return clean 404 (NEVER proxied to external host)."""
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{bottle_test_server}/non_existent_file.js", timeout=3.0)
    assert exc_info.value.code == 404
    err_body = json.loads(exc_info.value.read().decode("utf-8"))
    assert err_body["error"] == "Not Found"

def test_removed_proxy_routes_return_404(bottle_test_server):
    """Confirm /osiris-live, /_next, and /vendor return clean 404."""
    for path in ["/osiris-live", "/osiris-live/", "/_next/static/chunks/main.js", "/vendor/mapbox.js"]:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{bottle_test_server}{path}", timeout=3.0)
        assert exc_info.value.code == 404
        err_body = json.loads(exc_info.value.read().decode("utf-8"))
        assert err_body["error"] == "Not Found"

def test_directory_traversal_protection(bottle_test_server):
    """Confirm traversal attempts are blocked with 403 or 404."""
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{bottle_test_server}/../../etc/passwd", timeout=3.0)
    assert exc_info.value.code in (403, 404)
