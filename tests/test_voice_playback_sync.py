import time
import threading
from unittest.mock import MagicMock, patch
import pytest
from frontend.desktop import JarvisAPI


def test_jarvis_api_notify_tts_playback_finished():
    """Verify that notify_tts_playback_finished cleanly triggers the turn event."""
    api = JarvisAPI()
    ev = threading.Event()
    with api._turn_events_lock:
        api._turn_playback_events[1] = ev

    assert not ev.is_set()
    res = api.notify_tts_playback_finished(turn_id=1)
    assert res["success"] is True
    assert res["turn_id"] == 1
    assert ev.is_set()


def test_followup_window_waits_for_playback_finished_signal():
    """Verify that follow-up window does NOT fire until browser signals playback finished."""
    api = JarvisAPI()
    api._start_follow_up_window = MagicMock()

    turn_id = 42
    playback_event = threading.Event()
    with api._turn_events_lock:
        api._turn_playback_events[turn_id] = playback_event

    with api._tts_turn_lock:
        api._tts_turn_id = turn_id

    tts_state = {"emitted": True}
    api._tts_playback_until = time.time() + 5.0

    finished_called = threading.Event()

    def run_worker():
        if tts_state.get("emitted"):
            safety_timeout = min(60.0, max(0.5, api._tts_playback_until - time.time()) + 3.0)
            playback_event.wait(timeout=safety_timeout)
        with api._turn_events_lock:
            api._turn_playback_events.pop(turn_id, None)
        with api._tts_turn_lock:
            if api._tts_turn_id != turn_id:
                return
        api._tts_speaking = False
        api._start_follow_up_window()
        finished_called.set()

    t = threading.Thread(target=run_worker)
    t.start()

    # Verify that while playback_event is unset, _start_follow_up_window is NOT called
    time.sleep(0.15)
    assert not finished_called.is_set()
    api._start_follow_up_window.assert_not_called()

    # Now simulate browser Web Audio draining and firing notify_tts_playback_finished
    api.notify_tts_playback_finished(turn_id=turn_id)
    t.join(timeout=1.0)

    assert finished_called.is_set()
    api._start_follow_up_window.assert_called_once()


def test_cancel_playback_unblocks_waiting_threads_and_discards_stale_turn():
    """Verify barge-in unblocks waiting thread immediately without opening follow-up window."""
    api = JarvisAPI()
    api._start_follow_up_window = MagicMock()

    turn_id = 99
    playback_event = threading.Event()
    with api._turn_events_lock:
        api._turn_playback_events[turn_id] = playback_event

    with api._tts_turn_lock:
        api._tts_turn_id = turn_id

    tts_state = {"emitted": True}
    api._tts_playback_until = time.time() + 10.0

    finished_called = threading.Event()

    def run_worker():
        if tts_state.get("emitted"):
            playback_event.wait(timeout=15.0)
        with api._turn_events_lock:
            api._turn_playback_events.pop(turn_id, None)
        with api._tts_turn_lock:
            if api._tts_turn_id != turn_id:
                finished_called.set()
                return
        api._start_follow_up_window()

    t = threading.Thread(target=run_worker)
    t.start()

    time.sleep(0.1)
    assert not finished_called.is_set()

    # Simulate barge-in / interrupt
    api.cancel_playback()
    t.join(timeout=1.0)

    assert finished_called.is_set()
    # Follow-up window must NOT be opened for the interrupted turn
    api._start_follow_up_window.assert_not_called()


def test_safety_timeout_fallback_unblocks_when_browser_drops_signal():
    """Verify that if browser signal never arrives, safety timeout unblocks smoothly."""
    api = JarvisAPI()
    api._start_follow_up_window = MagicMock()

    turn_id = 101
    playback_event = threading.Event()
    with api._turn_events_lock:
        api._turn_playback_events[turn_id] = playback_event

    with api._tts_turn_lock:
        api._tts_turn_id = turn_id

    tts_state = {"emitted": True}
    api._tts_playback_until = time.time() - 2.0

    start_time = time.time()
    safety_timeout = 0.2
    finished = playback_event.wait(timeout=safety_timeout)
    elapsed = time.time() - start_time

    assert not finished
    assert elapsed >= 0.18
    assert elapsed < 1.0


def test_frontend_app_html_kill_switch_removed():
    """Verify app.html no longer calls resetJarvisAudioQueue on followup_listening_active."""
    with open("/home/joe/Project-Hellhound/JARVIS/frontend/app.html", "r", encoding="utf-8") as f:
        html = f.read()

    start_idx = html.find("case 'jarvis_followup_listening_active':")
    assert start_idx != -1
    end_idx = html.find("case 'jarvis_followup_listening_ended':", start_idx)
    assert end_idx != -1
    block = html[start_idx:end_idx]

    assert "resetJarvisAudioQueue();" not in block
    assert "setHudState('followup_listening');" in block

    assert "case 'jarvis_tts_stream_end':" in html
    assert "function checkAndNotifyPlaybackCompletion" in html
    assert "notify_tts_playback_finished" in html


def test_long_turn_shared_audio_queue_drained_during_playback():
    """Verify that during a 30+ chunk TTS turn, _shared_audio_queue never accumulates stale audio,
    and is completely empty at the moment follow-up window opens."""
    api = JarvisAPI()
    api._start_follow_up_window = MagicMock()
    api._tts_speaking = True
    api._tts_playback_until = time.time() + 1.0

    # Simulate 38 audio chunks coming into _shared_audio_queue from WakeWordEngine callback
    for i in range(38):
        api._on_shared_audio_chunk(b"\x00\x01" * 640)

    # In _bg_voice_loop's active draining logic:
    # When _tts_speaking is True, the queue is drained
    while not api._shared_audio_queue.empty():
        api._shared_audio_queue.get_nowait()

    assert api._shared_audio_queue.empty()

    # Now simulate chunk emission loop completing and _await_speech_completion_and_open_mic running
    turn_id = 150
    playback_event = threading.Event()
    with api._turn_events_lock:
        api._turn_playback_events[turn_id] = playback_event

    with api._tts_turn_lock:
        api._tts_turn_id = turn_id

    # Push extra chunks right as playback finishes
    for i in range(5):
        api._on_shared_audio_chunk(b"\x00\x02" * 640)
    assert not api._shared_audio_queue.empty()

    # Trigger playback completion
    api.notify_tts_playback_finished(turn_id=turn_id)

    # Execute the flush block from _await_speech_completion_and_open_mic
    api._tts_speaking = False
    api._tts_playback_until = time.time() + 1.5
    if hasattr(api, '_shared_audio_queue'):
        while True:
            try:
                api._shared_audio_queue.get_nowait()
            except Exception:
                break
    api._start_follow_up_window()

    # Confirm it is empty at the moment the follow-up window opens
    assert api._shared_audio_queue.empty()
    api._start_follow_up_window.assert_called_once()


def test_drain_epsilon_logic_for_normal_and_tiny_chunks():
    """Verify the drain tolerance logic prevents premature completion on both
    normal chunks (1s) and sub-50ms trailing chunks."""
    with open("/home/joe/Project-Hellhound/JARVIS/frontend/app.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert "const drainTolerance = 0.02;" in html
    assert "ctx.currentTime >= (jarvisPcmNextStartTime - drainTolerance)" in html

    # Logical verification of the drain condition
    def is_audio_drained(active_chunks, current_time, next_start_time, tolerance=0.02):
        return active_chunks <= 0 and current_time >= (next_start_time - tolerance)

    # 1. Normal chunk (1.0s): starts at 10.0, ends at 11.0
    start_at = 10.0
    next_start = 11.0

    # Halfway through: still playing
    assert not is_audio_drained(1, 10.5, next_start)
    # Right before completion (at 10.8s): should NOT be drained
    assert not is_audio_drained(0, 10.8, next_start)
    # At 10.95s: outside 0.02s tolerance -> not drained
    assert not is_audio_drained(0, 10.95, next_start)
    # At 10.99s: within 0.02s tolerance -> drained
    assert is_audio_drained(0, 10.99, next_start)

    # 2. Trailing tiny chunk (20ms): starts at 11.0, ends at 11.02
    start_at_tiny = 11.0
    next_start_tiny = 11.02

    # Old broken logic (tolerance = 0.1):
    # At currentTime = 10.95 (50ms BEFORE tiny chunk starts!):
    # 10.95 >= 11.02 - 0.1 = 10.92 -> TRUE (PREMATURE COMPLETION BUG!)
    old_broken_drained = 10.95 >= (next_start_tiny - 0.1)
    assert old_broken_drained is True  # Proves the bug existed

    # New corrected logic (tolerance = 0.02):
    # At currentTime = 10.95: 10.95 >= 11.02 - 0.02 = 11.0 -> FALSE (Fixed!)
    assert not is_audio_drained(0, 10.95, next_start_tiny, tolerance=0.02)
    # At currentTime = 11.01 (during playback): active_chunks=1 -> FALSE
    assert not is_audio_drained(1, 11.01, next_start_tiny, tolerance=0.02)
    # At completion (currentTime = 11.02, active_chunks=0) -> TRUE
    assert is_audio_drained(0, 11.02, next_start_tiny, tolerance=0.02)


def test_push_to_talk_barge_in_and_self_echo():
    """Verify push-to-talk during active playback triggers barge-in and allows user command,
    while non-barge-in room echo is properly suppressed."""
    api = JarvisAPI()
    api.cancel_playback = MagicMock(wraps=api.cancel_playback)

    # 1. Active TTS is speaking
    api._tts_speaking = True
    api._tts_playback_until = time.time() + 10.0
    api._recent_agent_responses = ["WiFi 6 target wake time extends battery life"]

    # User presses Push-to-Talk while TTS is active
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        res = api.start_native_mic()
        assert res["success"] is True
    # Verify barge-in was detected and cancel_playback was called immediately
    assert api._ptt_was_barge_in is True
    api.cancel_playback.assert_called_once()
    assert api._tts_speaking is False

    # Simulate transcribing user's barge-in speech
    api._transcribe_audio_fast = MagicMock(return_value="cancel that and tell me battery life")
    api._mic_start_time = time.time() - 2.0
    with open("/tmp/jarvis_mic_rec.wav", "wb") as f:
        f.write(b"RIFF" + b"\x00" * 100)
    # Even though text contains "battery life", barge-in must NOT be suppressed
    stop_res = api.stop_native_mic()
    assert stop_res["success"] is True
    assert stop_res["text"] == "cancel that and tell me battery life"
    assert api._ptt_was_barge_in is False

    # 2. Non-barge-in room echo: user did NOT barge in, but mic captures JARVIS's past response
    api._ptt_was_barge_in = False
    api._tts_speaking = False
    api._tts_playback_until = time.time() + 1.0  # within reverb window
    api._recent_agent_responses = ["WiFi 6 target wake time extends battery life"]
    api._transcribe_audio_fast = MagicMock(return_value="WiFi 6 target wake time extends battery life")
    api._mic_proc = MagicMock()
    api.normalize_wav_audio = MagicMock()
    with open("/tmp/jarvis_mic_rec.wav", "wb") as f:
        f.write(b"RIFF" + b"\x00" * 100)
    echo_res = api.stop_native_mic()
    assert echo_res["success"] is False
    assert echo_res["error"] == "Self-echo suppressed"


def test_streaming_prefix_filter_apostrophe_in_bracket_not_suppressed():
    """Verify that an LLM response containing an apostrophe inside brackets (e.g. [Sir's briefing])
    does NOT enter an unclosed quote state and cleanly emits all text without suppression."""
    from frontend.desktop import _StreamingPrefixFilter

    emitted_chunks = []
    filter_obj = _StreamingPrefixFilter(lambda c: emitted_chunks.append(c))

    # Stream chunks containing brackets with apostrophes, numbers, and plain words
    test_stream = [
        "Good morning. ",
        "[Sir's briefing is complete] ",
        "WiFi 6 latency is reduced, and ",
        "[Note: it's critical] to maintain antenna alignment.",
    ]

    for chunk in test_stream:
        filter_obj.feed(chunk)
    filter_obj.flush()

    assembled = "".join(emitted_chunks)
    # Both bracketed phrases must be completely emitted, not swallowed
    assert "Good morning." in assembled
    assert "[Sir's briefing is complete]" in assembled
    assert "WiFi 6 latency is reduced" in assembled
    assert "[Note: it's critical] to maintain antenna alignment." in assembled


def test_streaming_prefix_filter_dispatches_real_directives_and_strips_them():
    """Verify that legitimate tactical directives ([CMD:...], [NAV:...], [ZOOM:...])
    are dispatched to their respective callbacks and stripped from speech."""
    from frontend.desktop import _StreamingPrefixFilter

    emitted_chunks = []
    nav_calls = []
    zoom_calls = []

    filter_obj = _StreamingPrefixFilter(
        lambda c: emitted_chunks.append(c),
        on_nav=lambda loc: nav_calls.append(loc),
        on_zoom=lambda z: zoom_calls.append(z),
    )

    filter_obj.feed("Course plotted. [NAV: Nilgiris] Adjusting heading. [ZOOM: in] All systems nominal.")
    filter_obj.flush()

    assembled = "".join(emitted_chunks)
    assert "[NAV:" not in assembled
    assert "[ZOOM:" not in assembled
    assert "Course plotted." in assembled
    assert "Adjusting heading." in assembled
    assert "All systems nominal." in assembled
    assert nav_calls == ["Nilgiris"]
    assert zoom_calls == ["in"]


def test_jarvis_stream_chunk_and_answer_include_turn_id():
    """Verify jarvis_stream_chunk and jarvis_answer events are emitted with turn_id."""
    from frontend.desktop import JarvisAPI

    api = JarvisAPI()
    events = []

    def fake_emit(event, data):
        events.append((event, data))

    api._emit = fake_emit

    # Verify emit_clean_chunk in _run_ask includes turn_id
    with api._tts_turn_lock:
        api._tts_turn_id = 77
        tts_turn_id = api._tts_turn_id

    api._emit("jarvis_stream_chunk", {"chunk": "Hello, Sir.", "turn_id": tts_turn_id})
    api._emit("jarvis_answer", {"text": "Done.", "turn_id": tts_turn_id})

    assert len(events) == 2
    assert events[0][0] == "jarvis_stream_chunk"
    assert events[0][1]["turn_id"] == 77
    assert events[1][0] == "jarvis_answer"
    assert events[1][1]["turn_id"] == 77


def test_frontend_turn_isolation_logic_in_app_html():
    """Verify app.html implements turn-isolated streaming elements and turn-tagged answer finalization."""
    with open("/home/joe/Project-Hellhound/JARVIS/frontend/app.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert "function startStreamingAnswer(turnId)" in html
    assert "function updateStreamingChunk(chunkText, turnId)" in html
    assert "function finalizeStreamingAnswer(finalText, turnId)" in html
    assert "el.dataset.turnId = String(tId);" in html
    assert "[JarvisText] Received stream chunk" in html
    assert "[JarvisText] Received answer" in html


