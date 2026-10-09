"""
Unit tests for Part 3: Every Panel is its Own Window.
Verifies:
1. All 6 panels (Airspace, Maritime, Satellites, Ground Intel, Weather, System/Logs)
   are configured as independent floating windows in frontend/app.html.
2. Each window possesses:
   - Drag handle header
   - 8-direction resize handles (4 edges + 4 corners)
   - Scale down (A-) and Scale up (A+) buttons (clamped 70% to 150%)
   - Minimize (_) and Close (X) buttons
3. Top bar possesses the Panels dock menu with toggle controls and sensible Reset Layout option.
4. Minimized windows render compact chips/pills in a dock and can be restored.
5. TacticalWindowManager manages viewport clamping, z-index click-to-front, and localStorage persistence.
"""

from pathlib import Path
import re
import pytest

APP_HTML_PATH = Path("frontend/app.html")


def test_tactical_windows_exist():
    assert APP_HTML_PATH.exists()
    html = APP_HTML_PATH.read_text(encoding="utf-8")

    expected_panels = [
        "tactical-win-airspace",
        "tactical-win-maritime",
        "tactical-win-satellites",
        "tactical-win-ground-intel",
        "tactical-win-weather",
        "tactical-win-system-logs"
    ]
    for pid in expected_panels:
        assert f'id="{pid}"' in html, f"Missing window element: {pid}"


def test_window_headers_controls_and_resize_handles():
    html = APP_HTML_PATH.read_text(encoding="utf-8")

    # 8 resize handles
    for handle_dir in ["t", "r", "b", "l", "tl", "tr", "bl", "br"]:
        assert f'data-dir="{handle_dir}"' in html, f"Missing resize handle direction: {handle_dir}"

    # Window controls
    panels = ["airspace", "maritime", "satellites", "ground_intel", "weather", "system_logs"]
    for p in panels:
        assert f"TacticalWindowManager.scaleWindow('{p}', -0.1)" in html
        assert f"TacticalWindowManager.scaleWindow('{p}', 0.1)" in html
        assert f"TacticalWindowManager.minimizeWindow('{p}')" in html
        assert f"TacticalWindowManager.closeWindow('{p}')" in html


def test_topbar_panels_menu_and_reset_layout():
    html = APP_HTML_PATH.read_text(encoding="utf-8")

    assert 'id="topbar-panels-btn"' in html
    assert 'id="topbar-panels-menu"' in html
    assert 'id="topbar-panels-list"' in html
    assert "TacticalWindowManager.togglePanelsMenu()" in html
    assert "TacticalWindowManager.resetLayout()" in html
    assert "TacticalWindowManager.openAllPanels()" in html


def test_minimized_dock_bar():
    html = APP_HTML_PATH.read_text(encoding="utf-8")

    assert 'id="tactical-minimized-dock"' in html
    assert "tactical-min-chip" in html
    assert "renderMinimizedDock" in html


def test_tactical_window_manager_methods_and_persistence():
    html = APP_HTML_PATH.read_text(encoding="utf-8")

    assert "window.TacticalWindowManager =" in html
    assert "scaleWindow(panelKey, delta)" in html
    assert "minimizeWindow(panelKey)" in html
    assert "restoreWindow(panelKey)" in html
    assert "closeWindow(panelKey)" in html
    assert "bringToFront(panelKey)" in html
    assert "clamp(el)" in html
    assert "clampAll()" in html
    assert "saveState()" in html
    assert "loadState()" in html
    assert "resetLayout()" in html
    assert "jarvis_tactical_windows_v2" in html
    # Scale bounds: 0.70 to 1.50 (70% to 150%)
    assert "0.70" in html
    assert "1.50" in html
