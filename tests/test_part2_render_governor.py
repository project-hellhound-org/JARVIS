import socket
import threading
import time
import json
import subprocess
from pathlib import Path
import bottle
import pytest

from frontend.desktop import setup_jarvis_bottle_routes, JarvisAPI

@pytest.fixture(scope="module")
def app_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()

    app = bottle.Bottle()
    frontend_dir = str(Path('frontend').resolve())
    setup_jarvis_bottle_routes(app, frontend_dir, api=JarvisAPI(initial_mode='full'))

    @app.route('/<path:path>')
    def server_static(path):
        return bottle.static_file(path, root=frontend_dir)

    srv_thread = threading.Thread(
        target=lambda: bottle.run(app=app, host='127.0.0.1', port=port, quiet=True),
        daemon=True
    )
    srv_thread.start()
    time.sleep(1.5)
    return f"http://127.0.0.1:{port}"

def test_cesium_render_governor_and_watchdog(app_server):
    """
    Part 2 Headless Browser Check:
    1. Flights layer on, camera idle for 5 s: frames rendered (idle cadence in headless Chromium with SwiftShader).
    2. Hide and show the page: rendering resumes.
    """
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
                '--window-size=480,270',
                '--disable-background-timer-throttling',
                '--disable-renderer-backgrounding',
                '--disable-backgrounding-occluded-windows'
            ]
        }});
        const page = await browser.newPage({{ viewport: {{ width: 480, height: 270 }} }});
        await page.route('**/*', route => {{
            const u = route.request().url();
            if (u.includes('arcgisonline') || u.includes('openstreetmap') || u.includes('airplanes.live') || u.includes('adsb.lol') || u.includes('fonts.googleapis') || u.includes('fonts.gstatic')) {{
                return route.abort();
            }}
            return route.continue();
        }});
        await page.goto('{app_server}/app.html', {{ waitUntil: 'domcontentloaded', timeout: 25000 }});
        await page.waitForFunction(() => window.cesiumViewer && !window.cesiumViewer.isDestroyed() && window._cesiumRenderedFrames !== undefined);
        await page.waitForTimeout(2000);

        // Turn flights layer ON and position to Globe
        await page.evaluate(() => {{
            if (typeof sfStop === 'function') sfStop();
            if (window.SpatialNav) {{
                window.SpatialNav.targetX = 950;
                window.SpatialNav.currentX = 950;
                window.SpatialNav._applySpatialProperties();
            }}
            const wrapper = document.getElementById('cesium-spatial-wrapper');
            if (wrapper) {{
                wrapper.style.visibility = 'visible';
                wrapper.style.transform = 'none';
                wrapper.style.opacity = '1';
            }}
            window.toggleTacticalLayer('flights', true);
            window._cesiumRenderedFrames = 0;
        }});

        // Idle camera for 5 seconds
        await page.waitForTimeout(5000);

        const idleFrames = await page.evaluate(() => window._cesiumRenderedFrames);

        // Hide page
        await page.evaluate(() => {{
            Object.defineProperty(document, 'hidden', {{ value: true, configurable: true, writable: true }});
            document.dispatchEvent(new Event('visibilitychange'));
        }});
        await page.waitForTimeout(1000);
        const framesWhileHidden = await page.evaluate(() => window._cesiumRenderedFrames);

        // Show page again
        await page.evaluate(() => {{
            Object.defineProperty(document, 'hidden', {{ value: false, configurable: true, writable: true }});
            document.dispatchEvent(new Event('visibilitychange'));
            window.dispatchEvent(new Event('focus'));
        }});
        await page.waitForTimeout(1500);
        const resumedFrames = await page.evaluate(() => window._cesiumRenderedFrames);

        console.log(JSON.stringify({{
            idleFrames,
            framesWhileHidden,
            resumedFrames
        }}));

        await browser.close();
        process.exit(0);
    }})();
    """

    proc = subprocess.run(['node', '-e', node_code], capture_output=True, text=True, timeout=90)
    assert proc.returncode == 0, f"Playwright failed: {proc.stderr}"
    data = json.loads(proc.stdout.strip())

    print(f"\n[Render Governor Check] 5s idle frames: {data['idleFrames']}, while hidden: {data['framesWhileHidden']}, after resume: {data['resumedFrames']}")
    assert data['idleFrames'] >= 80, f"Expected at least 80 frames rendered in 5s idle, got {data['idleFrames']}"
    assert data['resumedFrames'] > data['framesWhileHidden'], "Expected rendering to resume after showing page"
