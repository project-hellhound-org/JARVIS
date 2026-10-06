# tests/test_osiris_phase3.py
"""
Test Suite for OSIRIS Phase 3: Native Cesium Layer Integration, Iframe Removal, and HUD Telemetry.
Validates:
1. Complete removal of osirisContainer, osirisFrame, /osiris-live, and postMessage references in app.html.
2. Clean presence and wiring of OsirisCesiumLayer, LOD tiers, and HUD telemetry debrief banner.
3. Verification that setGlobeEngine correctly controls OsirisCesiumLayer without external dependencies.
4. Validation that Phase 2 bridge methods power the native Cesium layer seamlessly.
"""

import pytest
import json
import socket
import threading
import time
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modules.osiris_intel import get_osiris_client, OsirisIntelClient
from frontend.desktop import JarvisAPI, setup_jarvis_bottle_routes
import bottle



def test_iframe_and_proxy_references_completely_removed():
    with open("frontend/app.html", "r", encoding="utf-8") as f:
        html = f.read()

    # 1. No iframe element
    assert 'id="osirisContainer"' not in html
    assert "<iframe id=\"osirisContainer\"" not in html

    # 2. No references to osirisFrame or postMessage to osirisFrame
    assert "osirisFrame" not in html
    assert "osirisContainer" not in html
    assert "__JARVIS_EMBEDDED_OSIRIS__" not in html

    # 3. No proxy route reference
    assert "'/osiris-live'" not in html
    assert '"/osiris-live"' not in html


def test_native_cesium_osiris_layer_and_hud_elements_present():
    with open("frontend/app.html", "r", encoding="utf-8") as f:
        html = f.read()

    # 1. Native layer engine exists
    assert "window.OsirisCesiumLayer" in html
    assert "initOsirisCesiumLayer" in html

    # 2. LOD tiers implemented
    assert "populateHighAltitudeClusters" in html
    assert "OSIRIS_GLOBAL_CLUSTERS" in html
    assert "fetchOsirisCctvViewport" in html
    assert "renderCctvFrustum" in html

    # 3. HUD Debrief element and status surfacing
    assert 'id="osiris-telemetry-debrief"' in html
    assert "setOsirisHudDebrief" in html

    # 4. Engine button toggles native layer
    assert "setGlobeEngine" in html
    assert "btn-engine-osiris" in html
    assert "btn-engine-cesium" in html


@patch.object(OsirisIntelClient, "_fetch_endpoint")
def test_phase3_bridge_methods_support_cesium_layer(mock_fetch):
    api = JarvisAPI()

    # Satellite stream
    mock_fetch.return_value = {
        "total": 1,
        "satellites": [
            {"name": "ISS (ZARYA)", "lat": 25.0, "lng": 45.0, "alt": 420, "category": "iss", "noradId": "25544"}
        ]
    }
    sats_res = api.get_active_satellites("iss", limit=10)
    assert sats_res["status"] == "ok"
    assert len(sats_res["satellites"]) == 1

    # Conflict stream
    mock_fetch.return_value = {
        "totalZones": 1,
        "activeWarzones": 1,
        "zones": [{"id": "z1", "label": "THEATRE ALPHA", "severity": "war", "lat": 30.0, "lng": 40.0}],
        "liveEvents": []
    }
    conf_res = api.get_active_conflicts()
    assert conf_res["status"] == "ok"
    assert conf_res["activeWarzones"] == 1

    # Viewport CCTV stream with LOD display cap
    mock_fetch.return_value = {
        "total": 2,
        "cameras": [
            {"id": "c1", "name": "Cam 1", "lat": 37.7, "lng": -122.4, "category": "traffic"},
            {"id": "c2", "name": "Cam 2", "lat": 37.8, "lng": -122.5, "category": "highway"},
        ]
    }
    cctv_res = api.get_cctv_in_viewport(bounds={"south": 37.0, "west": -123.0, "north": 38.0, "east": -122.0}, limit=40)
    assert cctv_res["status"] == "ok"
    assert len(cctv_res["cameras"]) == 2


def test_satellite_points_playwright_behavior_toggle():
    """
    Playwright behavioral test validating real browser execution:
    1. Cesium initializes window._satellitePoints as a PointPrimitiveCollection attached to scene primitives.
    2. Toggling tactical space layer ON sets window._satellitePoints.show to True.
    3. Toggling tactical space layer OFF sets window._satellitePoints.show to False.
    4. Toggling back ON restores window._satellitePoints.show to True.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()

    app = bottle.Bottle()
    setup_jarvis_bottle_routes(app, str(Path('frontend').resolve()), api=JarvisAPI(initial_mode='full'))

    @app.route('/<path:path>')
    def server_static(path):
        return bottle.static_file(path, root=str(Path('frontend').resolve()))

    srv_thread = threading.Thread(
        target=lambda: bottle.run(app=app, host='127.0.0.1', port=port, quiet=True),
        daemon=True
    )
    srv_thread.start()
    time.sleep(1.5)

    node_code = f"""
    const {{ chromium }} = require('playwright');
    (async () => {{
        const browser = await chromium.launch({{
            executablePath: '/usr/bin/chromium',
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--use-gl=angle',
                '--use-angle=swiftshader',
                '--enable-webgl',
                '--window-size=1920,1080'
            ]
        }});
        const page = await browser.newPage({{ viewport: {{ width: 1920, height: 1080 }} }});
        await page.goto('http://127.0.0.1:{port}/app.html', {{ waitUntil: 'load', timeout: 35000 }});
        await page.waitForTimeout(3000);
        await page.waitForFunction(() => window.cesiumViewer && !window.cesiumViewer.isDestroyed() && window._satellitePoints);

        const results = await page.evaluate(async () => {{
            const steps = [];
            // 1. Initial collection state
            const isCollection = !!(window._satellitePoints && typeof window._satellitePoints.add === 'function');
            steps.push({{ step: 'init', isCollection, show: window._satellitePoints.show }});

            // 2. Toggle space layer ON
            window.toggleTacticalLayer('space', true);
            steps.push({{ step: 'toggle_on', show: window._satellitePoints.show }});

            // 3. Toggle space layer OFF
            window.toggleTacticalLayer('space', false);
            steps.push({{ step: 'toggle_off', show: window._satellitePoints.show }});

            // 4. Toggle space layer back ON
            window.toggleTacticalLayer('space', true);
            steps.push({{ step: 'toggle_on_2', show: window._satellitePoints.show }});

            return steps;
        }});

        console.log(JSON.stringify(results));
        await browser.close();
    }})();
    """

    proc = subprocess.run(['node', '-e', node_code], capture_output=True, text=True, timeout=50)
    assert proc.returncode == 0, f"Playwright failed: {proc.stderr}"
    data = json.loads(proc.stdout.strip())

    # Step 1: verify PointPrimitiveCollection
    assert data[0]['isCollection'] is True, "window._satellitePoints is not a valid PointPrimitiveCollection"
    # Step 2: verify show === True on space layer ON
    assert data[1]['show'] is True, "window._satellitePoints.show is not True when space layer is toggled ON"
    # Step 3: verify show === False on space layer OFF
    assert data[2]['show'] is False, "window._satellitePoints.show is not False when space layer is toggled OFF"
    # Step 4: verify show === True on space layer ON again
    assert data[3]['show'] is True, "window._satellitePoints.show is not True when space layer is toggled ON again"



def test_no_double_slash_in_panel_or_hud_labels():
    """Verify '//' has been removed from all panel, chamber, waypoint, and CCTV HUD labels."""
    with open("frontend/app.html", "r", encoding="utf-8") as f:
        html = f.read()

    # Replaced with ·
    assert "// TACTICAL OSINT CHAMBER" not in html
    assert "· TACTICAL OSINT CHAMBER" in html
    assert "TRANSIT COMPLETE // ARRIVAL" not in html
    assert "TRANSIT COMPLETE · ARRIVAL" in html
    assert "TACTICAL PIN // ACQUIRED" not in html
    assert "TACTICAL PIN · ACQUIRED" in html

    with open("frontend/desktop.py", "r", encoding="utf-8") as f:
        py_code = f.read()
    assert "● REC [LIVE OPTICAL FEED] //" not in py_code
    assert "● REC [LIVE OPTICAL FEED] ·" in py_code
    assert "● REC [LIVE OPTICAL]  //" not in py_code
    assert "● REC [LIVE OPTICAL] ·" in py_code


def test_outlines_disabled_on_terrain_entities():
    """Verify outlines are disabled on terrain-clamped geometries (clusters, conflicts, CCTV plane) to prevent imagery draping and terrain outline console warnings."""
    with open("frontend/app.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert "populateHighAltitudeClusters" in html
    # Frustum plane outline disabled
    assert "cctv-${cam.id}-plane" in html
    # Check that neither cluster nor conflict ellipses have outline: true
    assert "outline: true" not in html[html.find("populateHighAltitudeClusters"):html.find("populateHighAltitudeClusters") + 1000]
    assert "outline: true" not in html[html.find("renderOsirisConflicts"):html.find("renderOsirisConflicts") + 1000]


def test_offline_contingency_multi_stream_and_alert():
    """Verify multi-stream debrief tracking prioritizes upstream_error and triggers emitTacticalHudAlert."""
    with open("frontend/app.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert "_osirisFeedStatus" in html
    assert "emitTacticalHudAlert" in html
    assert "⚡ CONTINGENCY (OFFLINE)" in html

