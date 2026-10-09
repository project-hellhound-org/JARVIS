"""
Tests for speech serialization & anti-stutter buffer,
and autonomous tactical navigation agency in J.A.R.V.I.S.
"""

import re
from pathlib import Path
from core.jarvis_reasoning_loop import JarvisCognitiveLoop


def test_app_html_pcm_jitter_buffer():
    """Verify frontend/app.html enqueueJarvisPcmChunk implements a jitter buffer to prevent stutter."""
    html = Path("frontend/app.html").read_text(encoding="utf-8")
    assert "const isUnderRun = (activePlayingChunkCount === 0 || now >= jarvisPcmNextStartTime);" in html
    assert "const leadTime = isUnderRun ? 0.18 : 0.02;" in html
    assert "const startAt = Math.max(now + leadTime, jarvisPcmNextStartTime);" in html


def test_desktop_speech_lock_and_no_premature_interrupt():
    """Verify desktop.py serializes speech with _speak_lock and does not emit jarvis_interrupt_speech on completion."""
    desktop_code = Path("frontend/desktop.py").read_text(encoding="utf-8")
    assert "self._speak_lock = threading.Lock()" in desktop_code
    assert "with getattr(self, '_speak_lock', threading.Lock()):" in desktop_code

    speak_fn_match = re.search(r"def _speak_and_suppress_echo\(.*?\n(?=    def |\Z)", desktop_code, re.DOTALL)
    assert speak_fn_match is not None
    speak_fn = speak_fn_match.group(0)
    assert 'self._emit("jarvis_interrupt_speech"' not in speak_fn


def test_random_place_and_stuffs_goal_planning():
    """Verify 'navigate to a place, to a random place and show me some stuffs' triggers nav and ground_intel."""
    loop = JarvisCognitiveLoop()
    prompt = "navigate to a place, to a random place and show me some stuffs"
    steps = loop.analyze_goal(prompt)

    actions = [s["action"] for s in steps]
    assert "nav" in actions
    assert "ground_intel" in actions

    nav_step = next(s for s in steps if s["action"] == "nav")
    assert nav_step["location"] == "random"
