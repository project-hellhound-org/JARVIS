# frontend/hud_panel.py
"""
J.A.R.V.I.S. Action HUD Panel Overlay Generator.
Creates live visual pop-up cards and HTML overlay displays whenever J.A.R.V.I.S.
searches the web, opens an app, checks calendar/inbox, inspects code, or modifies configs.
Includes J.A.R.V.I.S.'s spoken dialogue: "Here, see the screen, partner — I found this!"
"""

import os
import json
import webbrowser
import time
from pathlib import Path
from typing import Dict, Any, Optional

BASE_DIR = Path(__file__).parent.parent.resolve()
HUD_STATE_FILE = BASE_DIR / "data" / "hud_panel_active.json"
HUD_HTML_FILE = BASE_DIR / "data" / "hud_action_display.html"


class HUDPanelManager:
    def __init__(self):
        os.makedirs(HUD_STATE_FILE.parent, exist_ok=True)

    def show_action_hud(
        self,
        title: str,
        action_type: str,
        details: str,
        preview_link_or_file: Optional[str] = None,
        auto_open_browser: bool = False
    ) -> Dict[str, Any]:
        """
        Generate a live J.A.R.V.I.S. HUD action card and save HUD overlay state.
        """
        hud_data = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "title": title,
            "action_type": action_type,
            "details": details,
            "preview": preview_link_or_file or "N/A",
            "spoken_intro": "Here, see the screen, partner — I found this!",
            "status": "LIVE HUD ACTIVE"
        }

        # Save JSON state for Desktop / TUI
        try:
            with open(HUD_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(hud_data, f, indent=2)
        except Exception as e:
            print(f"[hud_panel] Error writing HUD state: {e}")

        # Render HTML HUD overlay panel
        self.render_html_hud(hud_data)

        if auto_open_browser:
            try:
                webbrowser.open(f"file://{HUD_HTML_FILE}")
            except Exception:
                pass

        return hud_data

    def render_html_hud(self, data: Dict[str, Any]):
        """Render a sleek, J.A.R.V.I.S.-style cyberpunk dark-mode HTML HUD overlay panel."""
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>J.A.R.V.I.S. — ACTION HUD</title>
    <style>
        body {{
            background-color: #0b0f19;
            color: #00f0ff;
            font-family: 'Consolas', 'Courier New', monospace;
            margin: 0;
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }}
        .hud-card {{
            border: 2px solid #00f0ff;
            box-shadow: 0 0 25px rgba(0, 240, 255, 0.4);
            border-radius: 12px;
            background: linear-gradient(135deg, rgba(15,23,42,0.95), rgba(11,15,25,0.98));
            width: 650px;
            padding: 25px;
            position: relative;
        }}
        .hud-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid rgba(0,240,255,0.3);
            padding-bottom: 12px;
            margin-bottom: 20px;
        }}
        .hud-title {{
            font-size: 1.3rem;
            font-weight: bold;
            color: #ffffff;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .hud-badge {{
            background: #00f0ff;
            color: #0b0f19;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.8rem;
            font-weight: bold;
        }}
        .hud-body {{
            line-height: 1.6;
            font-size: 0.95rem;
            color: #e2e8f0;
            background: rgba(0,0,0,0.3);
            padding: 15px;
            border-radius: 8px;
            border-left: 4px solid #38bdf8;
            margin-bottom: 20px;
        }}
        .hud-preview {{
            background: rgba(15, 23, 42, 0.8);
            border: 1px dashed #38bdf8;
            padding: 12px;
            border-radius: 6px;
            font-size: 0.85rem;
            color: #94a3b8;
            word-break: break-all;
        }}
        .hud-footer {{
            margin-top: 20px;
            text-align: right;
            font-size: 0.8rem;
            color: #64748b;
        }}
    </style>
</head>
<body>
    <div class="hud-card">
        <div class="hud-header">
            <div class="hud-title">⚡ J.A.R.V.I.S. ACTION HUD</div>
            <div class="hud-badge">{data.get('action_type', 'ACTION')}</div>
        </div>
        <div class="hud-body">
            <p><strong>Partner Voice Notice:</strong> "{data.get('spoken_intro', '')}"</p>
            <p><strong>Action Title:</strong> {data.get('title', '')}</p>
            <p><strong>Execution Details:</strong> {data.get('details', '')}</p>
        </div>
        <div class="hud-preview">
            <strong>TARGET / PREVIEW LINK:</strong><br>
            {data.get('preview', 'N/A')}
        </div>
        <div class="hud-footer">
            TIMESTAMP: {data.get('timestamp', '')} | STATUS: ACTIVE
        </div>
    </div>
</body>
</html>
"""
        try:
            with open(HUD_HTML_FILE, "w", encoding="utf-8") as f:
                f.write(html_content)
        except Exception as e:
            print(f"[hud_panel] Error saving HTML HUD: {e}")
