import re
from pathlib import Path
from unittest.mock import MagicMock
from frontend.desktop import JarvisAPI

def test_hud_galaxy_default_unshrunk_scale():
    """Verify that 3D Knowledge Graph initializes with calibrated spherical bounds."""
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    assert "GRAPH_RADIUS" in app_html
    assert "PerspectiveCamera" in app_html
    assert "controls.minDistance" in app_html

def test_corner_settings_gear_and_clean_topbar():
    """Verify corner settings gear button is present and redundant text buttons are removed."""
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    assert 'id="corner-settings-btn"' in app_html
    assert 'class="corner-gear-btn"' in app_html
    assert 'onclick="window.openSettingsOverlay()"' in app_html
    # Redundant text settings buttons removed from top bar and spatial dock
    assert 'id="spatial-dock-settings-btn"' not in app_html
    assert 'id="top-bar-settings-btn"' not in app_html

def test_3screen_spatial_dock_and_the_cold_room():
    """Verify 3-screen spatial dock structure and The Cold Room chamber."""
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    # Screen 1: The Cold Room
    assert 'id="dock-btn-coldroom"' in app_html
    assert 'window.glideToSpatial(-950)' in app_html
    assert 'THE COLD ROOM' in app_html
    assert 'id="cold-room-wrapper"' in app_html
    assert 'id="cr-graph-canvas"' in app_html
    assert 'id="cr-feed-scroll"' in app_html
    assert 'id="cr-inspector-active"' in app_html

    # Screen 2: Tactical HUD
    assert 'id="dock-btn-hud"' in app_html
    assert 'window.glideToSpatial(0)' in app_html
    assert 'TACTICAL HUD' in app_html

    # Screen 3: World Telemetry
    assert 'id="dock-btn-globe"' in app_html
    assert 'window.glideToSpatial(950)' in app_html
    assert 'WORLD TELEMETRY' in app_html

    # Edge navigation handles
    assert 'id="spatial-edge-right"' in app_html
    assert 'id="spatial-edge-left"' in app_html

def test_coldroom_controllers_and_spatial_nav():
    """Verify ColdRoomGraph and ColdRoom objects and 3-screen SpatialNav in JS."""
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    assert "const ColdRoomGraph = {" in app_html
    assert "const ColdRoom = {" in app_html
    assert "window.ColdRoom = ColdRoom;" in app_html
    assert "window.ColdRoomGraph = ColdRoomGraph;" in app_html
    assert "SpatialNav.onRightEdgeClick()" in app_html
    assert "SpatialNav.onLeftEdgeClick()" in app_html
    assert "--spatial-abs-ratio" in app_html

def test_auto_glide_to_coldroom_on_investigation():
    """Verify scan_status and handleSend auto-glide into The Cold Room."""
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    assert "window.glideToSpatial(-950)" in app_html
    assert "ColdRoom.setStatus" in app_html
    assert "ColdRoom.addEntity" in app_html
    assert "ColdRoom.finish" in app_html

def test_desktop_spatial_voice_commands():
    """Verify desktop.py recognizes spatial screen voice commands."""
    api = JarvisAPI(initial_mode="full")
    emitted = []
    api._emit = lambda event, data: emitted.append((event, data))
    api._run_ask = MagicMock()

    # Voice command: switch to cold room
    api._run_process_input("switch to cold room")
    assert any(ev[0] == "glide_to_coldroom" for ev in emitted)

    # Voice command: return to hud
    emitted.clear()
    api._run_process_input("switch to tactical hud")
    assert any(ev[0] == "glide_to_hud" for ev in emitted)

    # Voice command: switch to world telemetry
    emitted.clear()
    api._run_process_input("switch to world telemetry")
    assert any(ev[0] == "glide_to_telemetry" for ev in emitted)

def test_coldroom_transparent_cosmic_styling():
    """Verify The Cold Room uses transparent glass panels and dedicated starfield matching the cosmic space theme."""
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    # Should not have opaque solid blue walls
    assert "background: radial-gradient(circle at 50% 25%, #080C14 0%, #030509 100%);" not in app_html
    # Should have transparent background on #cold-room-wrapper
    assert "#cold-room-wrapper {\n  position: fixed;\n  inset: 0;\n  width: 100vw;\n  height: 100vh;\n  z-index: 15;\n  pointer-events: none;\n  overflow: hidden;\n  display: flex;\n  flex-direction: column;\n  background: transparent !important;" in app_html
    # Center topology graph must be borderless and transparent, floating in deep space
    assert ".cr-pane.cr-pane-graph {\n  background: transparent !important;\n  border: none !important;\n  box-shadow: none !important;" in app_html
    # Dedicated starfield cosmos at x=-950 for The Cold Room
    assert "coldRoomCosmos.position.set(-950, 0, 0);" in app_html
    assert "window._coldRoomCosmosGroup = coldRoomCosmos;" in app_html

def test_hud_galaxy_drag_does_not_move_spatial_screen():
    """Verify interacting with the 3D Knowledge Graph does not trigger accidental screen gliding."""
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    assert "JarvisKnowledgeGraph" in app_html
    assert "#jarvis-3d-kg-mount" in app_html
    assert "controls.enableRotate" in app_html

def test_spatial_drag_allowed_outside_hud_orb():
    """Verify pointerdown outside HUD panels allows spatial drag to other screens."""
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    assert "onPointerDown" in app_html
    assert "SpatialNav" in app_html

def test_cesium_globe_isolated_to_screen_3():
    """Verify Cesium 3D Globe is strictly isolated to Screen 3 (World Telemetry) and hidden on Cold Room and HUD."""
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")

    # 1. Initial CSS state must hide globe offscreen with 0 opacity, avoiding brittle negative calc in WebKitGTK
    assert "visibility: hidden;\n  opacity: 0;\n  transform: translate3d(200vw, 0, 0);" in app_html
    assert "opacity: calc((var(--spatial-ratio" not in app_html

    # 2. #shell CSS must not rely on brittle CSS calc for spatial positioning
    assert "transform: translate3d(calc(-105vw" not in app_html

    # 3. SpatialNav must explicitly hide #cesium-spatial-wrapper when currentX <= 100
    assert "cesiumWrap.style.visibility = 'hidden';" in app_html
    assert "cesiumWrap.style.transform = 'translate3d(200vw, 0, 0)';" in app_html
    assert "cesiumWrap.style.opacity = '0';" in app_html

    # 4. SpatialNav must only make globe visible and compute progress when currentX > 100
    assert "if (this.currentX > 100) {" in app_html
    assert "cesiumWrap.style.visibility = 'visible';" in app_html
    assert "const globeProgress = Math.max(0, Math.min(1, (this.currentX - 100) / 750));" in app_html

    # 5. SpatialNav must explicitly manage #shell spatial transforms and hide when absRatio >= 0.85
    assert "shell.style.visibility = 'visible';" in app_html
    assert "shell.style.visibility = 'hidden';" in app_html
    assert "shell.style.transform = `translate3d(${shellTx}vw, 0, ${shellTz}px) rotateY(${shellRotY}deg)`" in app_html

    # 6. SpatialNav exported to window
    assert "window.SpatialNav = SpatialNav;" in app_html

def test_cesium_token_safety_and_radio_eager_load_prevention():
    """Verify Cesium Ion token sanitization, error handlers, and radio lazy loading."""
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")

    # 1. isRevokedOrDummyCesiumToken helper present
    assert "window.isRevokedOrDummyCesiumToken = function(tok)" in app_html
    assert "uJTR-pSh-JwPCONfcq" in app_html

    # 2. Cesium Terrain attaches errorEvent listener to intercept 401s without console.error('one')
    assert "terrain.errorEvent.addEventListener" in app_html

    # 3. Radio tuner defers audio.load() and does not eagerly resolve on startup
    assert "audio.dataset.pendingSrc = station.streamUrl;" in app_html
    assert "icecast.liveatc.net" not in app_html



