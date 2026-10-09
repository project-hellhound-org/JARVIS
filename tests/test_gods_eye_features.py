import re
from pathlib import Path

def test_gods_eye_street_traffic_subsystem():
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    assert "_trafficDataSource = new Cesium.CustomDataSource('traffic-layer')" in app_html
    assert "TRAFFIC_CORRIDORS" in app_html
    assert "austin-ih35" in app_html
    assert "delhi-rajpath" in app_html
    assert "traffic-sync-hud-banner" in app_html
    assert "REFRESHING LIVE DATA" in app_html
    assert "SYNCING LIVE TRAFFIC FLOW" in app_html
    assert "_trafficVehicles.forEach" in app_html

def test_gods_eye_3d_buildings_materials():
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    assert "Cesium.createOsmBuildingsAsync()" in app_html
    assert "buildingsTileset.maximumMemoryUsage = 128" in app_html
    assert "color('#0A192F', 0.45)" not in app_html
    assert "color('#D4D4D8', 0.95)" in app_html
    assert "DEFAULT_CESIUM_ION_TOKEN" in app_html

def test_gods_eye_tactical_radio_scanner():
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    assert "noaa_monterey" in app_html
    assert "NOAA Weather Radio KEC49" in app_html
    assert "162.400 MHz" in app_html
    assert "radio-dial-needle" in app_html
    assert "radio-dial-scale" in app_html
    assert "SNAPS TO AVAILABLE ST." in app_html
    assert "secret_agent" not in app_html
    assert "window.filterRadioByCategory" in app_html
    assert "window.cycleRadioStation" in app_html

def test_gods_eye_bottom_voice_dock_and_equalizer():
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    assert "gods-eye-bottom-dock" in app_html
    # Other two buttons removed per user directive; only the mic widget is kept
    assert "dock-btn-location" not in app_html
    assert "dock-btn-presets" not in app_html
    assert "dock-voice-widget" in app_html
    assert "dock-mic-btn" in app_html
    assert "dock-voice-eq" in app_html
    assert "voice-eq-bars" in app_html
    assert "window.updateGodsEyeVoiceWidget" in app_html
    # Subtext removed per user directive: "no need for more text"
    assert "dock-voice-subtext" not in app_html
    # Click-to-toggle mic feature (mouse hold removed per user directive)
    assert "window.triggerVoiceMicCommand" in app_html
    assert "window.toggleVoiceMicCommand" in app_html
    assert "window.onVoiceMicPointerDown" not in app_html
    # Orange active theme (changed from green to orange)
    assert "#dock-voice-widget.listening #dock-mic-btn" in app_html
    assert "#FF9D2E" in app_html

    # Faded turned-off state & one-click unwanted speech cancellation
    assert "#dock-voice-widget.mic-off" in app_html
    assert "opacity: 0.45" in app_html
    assert "stopMicListening(true)" in app_html

    # Acoustic feedback: speechReady sound when stopping listening & ready to speak, plus micStart / micCancel
    assert "speechReady()" in app_html
    assert "micStart()" in app_html
    assert "micCancel()" in app_html
    assert "window.JarvisSFX.speechReady()" in app_html or "JarvisSFX.speechReady" in app_html

def test_gods_eye_collapsible_chat_panel():
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    assert "chat-panel-header" in app_html
    assert "chat-panel-collapse-btn" in app_html
    assert "composer-collapse-btn" in app_html
    assert "chat-expand-launcher" in app_html
    assert "window.toggleChatPanel" in app_html
    assert "chat-panel-collapsed" in app_html

def test_gods_eye_deep_space_and_orbital_aircraft():
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    # Aircraft must be visible from space (full globe orbit) without distance culling
    assert "new Cesium.DistanceDisplayCondition(35000, Number.POSITIVE_INFINITY)" in app_html
    # Deep space cosmos starfield around Screen 2 globe
    assert "window._globeCosmosGroup = globeCosmos" in app_html
    assert "scene.globe.showGroundAtmosphere = true" in app_html
    # Sharp diamond celestial star texture
    assert "createSharpStarTexture" in app_html
    # God's Eye orbital reconnaissance aperture vignette (Image 5 Parity)
    assert "orbital-space-aperture" in app_html

def test_gods_eye_vad_auto_endpointing_and_barge_in():
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    desktop_py = Path("frontend/desktop.py").read_text(encoding="utf-8")

    # Streaming VAD with silence hangover and safety limits
    assert "VAD_SILENCE_HANGOVER_MS = 1000" in app_html
    assert "VAD_MAX_SPEECH_DURATION_MS = 15000" in app_html
    assert "VAD_SILENCE_ABORT_MS = 6000" in app_html
    assert "startVadMonitor" in app_html
    assert "stopMicListening(true)" in app_html or "stopMicListening(true)" in app_html.lower()

    # Instant Barge-In TTS Cancellation
    assert "Barge-in triggered" in app_html
    assert "cancel_playback" in app_html
    assert "def cancel_playback(self):" in desktop_py
    assert "def cancel_native_mic(self):" in desktop_py

    # Backend VAD speech end auto-endpoints listening in frontend
    assert "Backend VAD detected speech end" in app_html

def test_gods_eye_optical_filters_and_style_routing():
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    desktop_py = Path("frontend/desktop.py").read_text(encoding="utf-8")

    # CSS Optical Canvas Filters
    assert "#cesiumContainer.optical-flir canvas" in app_html
    assert "#cesiumContainer.optical-nvg canvas" in app_html
    assert "#cesiumContainer.optical-cyber canvas" in app_html

    # Visual Style Switch Event & Dynamic Class Handling
    assert "case 'set_earth_visual_style':" in app_html
    assert "classList.add('optical-flir')" in app_html

    # Backend streaming directive and layer alias routing
    assert "def _execute_tactical_style(self" in desktop_py
    assert "STYLE" in desktop_py
    assert "thermal_optics" in desktop_py

def test_gods_eye_target_locking_and_telemetry():
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")

    # Inspect card target locking
    assert "window.lockTarget(data._entity, data)" in app_html
    assert "window.unlockTarget" in app_html
    assert "viewer.trackedEntity = entity" in app_html
    assert "LOCK AIRCRAFT" in app_html
def test_gods_eye_multimodal_screen_and_recon_patrol():
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    desktop_py = Path("frontend/desktop.py").read_text(encoding="utf-8")

    # 1. Point, Speak, Act Multimodal Screen Intelligence
    assert "def _get_cursor_and_window_context(self" in desktop_py
    assert "def _annotate_screenshot_with_cursor(self" in desktop_py
    assert "_capture_desktop_screenshot" in desktop_py
    assert "look at this" in desktop_py
    assert "explain this error" in desktop_py
    assert "fix this code" in desktop_py
    assert "what am i pointing at" in desktop_py
    assert "[CMD: <command>]" in desktop_py

    # 2. NASA FIRMS Thermal Baseline Hotspots
    assert "def get_firms_hotspots(" in desktop_py
    assert "Amazon Basin" in desktop_py
    assert "Congo Basin" in desktop_py
    assert "Pantanal Wetland" in desktop_py

    # 3. Autonomous Global Recon Patrol Engine
    assert "patrol-toggle-btn" in app_html
    assert "window.JarvisPatrol = JarvisPatrol" in app_html
    assert "AUSTIN IH-35" in app_html
    assert "TOKYO SHIBUYA" in app_html
    assert "DELHI RAJPATH" in app_html
    assert "LONDON CITY" in app_html
    assert "PACIFIC FAULT ARC" in app_html
    assert "GLOBAL SYNOPTIC VIEW" in app_html
    assert "case 'control_patrol':" in app_html
    assert "def _execute_tactical_patrol(" in desktop_py
    assert "PATROL" in desktop_py
    assert "m_patrol" in desktop_py
    assert "setTimeout(() => {" in app_html
    assert "9000" in app_html

def test_hud_top_notch_and_low_latency_pipeline():
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")
    desktop_py = Path("frontend/desktop.py").read_text(encoding="utf-8")
    voice_py = Path("narrative/jarvis_voice.py").read_text(encoding="utf-8")
    jarvis_py = Path("jarvis.py").read_text(encoding="utf-8")

    # 1. Top-Center Dynamic Notch HUD (Zero logos, zero mic icon, wave ribbon + clean text)
    assert "jarvis-hud-notch-wrapper" in app_html
    assert "jarvis-hud-notch" in app_html
    assert "hud-wave-container" in app_html
    assert "hud-wave-canvas" in app_html
    assert "hud-text-container" in app_html
    assert "hud-notch-text" in app_html
    assert "top-bar-hud-btn" in app_html
    assert "body.hud-mode" in app_html
    # Verifying no logo and no mic icon inside the HUD notch
    assert "hud-logo" not in app_html
    assert "hud-mic-btn" not in app_html

    # 2. HUD JS Controller, Notification Drop Bar & Spacebar Push-to-Talk Lifecycle
    assert "window.setHudMode" in app_html
    assert "window.toggleHudMode" in app_html
    assert "window.updateVoiceOsState" in app_html
    assert "window.dropHudNotch" in app_html
    assert "window.retractHudNotch" in app_html
    assert "window.scheduleHudDismiss" in app_html
    assert "jarvis-hud-notch.dropped" in app_html
    assert "startHudWave" in app_html
    assert "stopHudWave" in app_html
    assert "case 'set_hud_mode':" in app_html
    assert "isSpacebarPushToTalk" in app_html
    assert "Alt" in app_html and ("'v'" in app_html or "'V'" in app_html)
    assert "mode=hud" in app_html

    # 3. Python Backend Dual-Mode & Streaming Directives
    assert "def set_window_mode(self, mode: str)" in desktop_py
    assert "def toggle_hud_mode(self)" in desktop_py
    assert "def snap_hud_to_top(self)" in desktop_py
    assert "_snap_hud_window_to_top_center" in desktop_py
    assert "def resize_hud_window" in desktop_py or "resize_voiceos_window" in desktop_py
    assert "def _execute_tactical_window(self, mode_spec: str)" in desktop_py
    assert "m_window" in desktop_py
    assert "on_window" in desktop_py
    assert "switch to hud" in desktop_py
    assert "expand to god's eye" in desktop_py

    # 4. Low-Latency Voice Pipeline: Clause Streaming & Fish Audio Tuning
    assert 'latency="low"' in voice_py
    assert "chunk_length=100" in voice_py
    assert "clause_regex = r'[,:;—–]\\s+'" in desktop_py or "clause_regex" in desktop_py
    assert "sent_count == 0" in desktop_py

    # 5. CLI Flags & Direct Boot
    assert "--hud" in jarvis_py
    assert "--mini" in jarvis_py
    assert "--pill" in jarvis_py
    assert 'launch_desktop(mode="hud")' in jarvis_py or 'launch_desktop(mode=' in jarvis_py
    assert 'def launch(self, mode: str = "full"):' in desktop_py


def test_multidomain_flight_differentiation_and_filters():
    """Verify multi-domain aircraft telemetry differentiation, filters, and Cesium safety."""
    app_html = Path("frontend/app.html").read_text(encoding="utf-8")

    # 1. Category Classification & Color Standards
    assert "classifyTacticalAircraft(ac)" in app_html
    assert "#22C55E" in app_html  # Military Green
    assert "#B45309" in app_html  # PIA Brown
    assert "#D97706" in app_html  # LADD Amber/Brown
    assert "#EF4444" in app_html  # Alert Red
    assert "#38BDF8" in app_html  # Commercial Cyan

    # 2. Filter Sub-Bar & Buttons in Spatial HUD Rail
    assert "id=\"flight-category-filters\"" in app_html
    assert "id=\"fcat-all\"" in app_html
    assert "id=\"fcat-mil\"" in app_html
    assert "id=\"fcat-pia\"" in app_html
    assert "id=\"fcat-ladd\"" in app_html
    assert "id=\"fcat-emergency\"" in app_html
    assert "id=\"fcat-commercial\"" in app_html
    assert ".flight-cat-btn" in app_html
    assert "window.setFlightCategoryFilter" in app_html

    # 3. Target Inspection Card Category Stamping
    assert "AIRFRAME / TYPE:" in app_html
    assert "badgeText" in app_html

    # 4. Immediate Head-level Cesium Keyless Clearance
    assert "Cesium.Ion.defaultAccessToken = '';" in app_html



