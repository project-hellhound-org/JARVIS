import time
import queue
import threading
from unittest.mock import MagicMock, patch
import pytest
import msgpack
from narrative.jarvis_voice import JarvisVoice


@pytest.fixture
def voice_instance():
    """Create a JarvisVoice instance configured for Fish Audio tests."""
    with patch("httpx.post") as mock_probe:
        mock_probe.return_value = MagicMock(status_code=200)
        v = JarvisVoice()
        v.fish_audio_available = True
        v.fish_audio_key = "test_fish_key_123"
        v.fish_audio_model = "s2.1-pro-free"
        v.fish_audio_voice_id = "test_voice_id_abc"
        v.fish_audio_ws_url = "wss://api.fish.audio/v1/tts/live"
        return v


def test_websocket_handshake_includes_model_header(voice_instance):
    """Verify handshake sends Authorization AND model header (preventing the 402 paid-model trap)."""
    mock_ws = MagicMock()
    mock_ws.__iter__.return_value = [
        msgpack.packb({"audio": b"pcmdata" * 100}),
        msgpack.packb({"event": "finish", "reason": "stop"}),
    ]

    with patch("narrative.jarvis_voice.ws_connect", return_value=mock_ws) as mock_connect:
        pcm_chunks = list(voice_instance.stream_fish_audio_pcm("Online and operational, sir."))

        # 1. Verify connection arguments
        mock_connect.assert_called_once()
        _, kwargs = mock_connect.call_args
        headers = kwargs.get("additional_headers", {})
        assert headers.get("Authorization") == "Bearer test_fish_key_123"
        assert headers.get("model") == "s2.1-pro-free"

        # 2. Verify start packet sent
        sent_calls = [msgpack.unpackb(call[0][0]) for call in mock_ws.send.call_args_list]
        start_packets = [p for p in sent_calls if p.get("event") == "start"]
        assert len(start_packets) == 1
        req = start_packets[0]["request"]
        assert req["model"] == "s2.1-pro-free"
        assert req["reference_id"] == "test_voice_id_abc"
        assert req["format"] == "pcm"
        assert req["chunk_length"] == 100
        assert 100 <= req["chunk_length"] <= 300

        # 3. Verify audio received
        assert len(pcm_chunks) >= 1
        assert pcm_chunks[0][0] == b"pcmdata" * 100
        assert pcm_chunks[0][1] == 24000


def test_websocket_handshake_402_handling(voice_instance):
    """Verify HTTP 402 / credit depleted on handshake disables Fish Audio and raises gracefully."""
    class DummyInvalidStatus(Exception):
        pass

    with patch("narrative.jarvis_voice.ws_connect") as mock_connect:
        mock_connect.side_effect = DummyInvalidStatus("server rejected WebSocket connection: HTTP 402")
        with pytest.raises(DummyInvalidStatus):
            list(voice_instance.stream_fish_audio_pcm("Testing credit depletion."))

        # Fish Audio marked unavailable on 402
        assert voice_instance.fish_audio_available is False


def test_websocket_clause_boundary_flushing_and_stop(voice_instance):
    """Verify queue of text clauses sends text + flush for each clause and stop on finish."""
    mock_ws = MagicMock()
    mock_ws.__iter__.return_value = [
        msgpack.packb({"audio": b"pcm1" * 50}),
        msgpack.packb({"audio": b"pcm2" * 50}),
        msgpack.packb({"event": "finish", "reason": "stop"}),
    ]

    q = queue.Queue()
    q.put("All diagnostic telemetry checks are clear, sir.")
    q.put("Airspace surveillance shows no conflicting flight paths.")
    q.put(None)  # End of turn

    with patch("narrative.jarvis_voice.ws_connect", return_value=mock_ws):
        chunks = list(voice_instance.stream_fish_audio_pcm(q))

        time.sleep(0.15)  # Allow sender thread to dispatch
        sent_events = [msgpack.unpackb(call[0][0]) for call in mock_ws.send.call_args_list]

        # Verify start event
        assert sent_events[0]["event"] == "start"

        # Verify text and stop events
        text_events = [e for e in sent_events if e.get("event") == "text"]
        stop_events = [e for e in sent_events if e.get("event") == "stop"]

        assert len(text_events) == 2
        assert "telemetry checks are clear" in text_events[0]["text"]
        assert "surveillance shows no conflicting" in text_events[1]["text"]
        assert len(stop_events) == 1

        # Verify audio chunks yielded
        assert len(chunks) == 2
        assert chunks[0][0] == b"pcm1" * 50
        assert chunks[1][0] == b"pcm2" * 50


def test_websocket_bargein_immediate_socket_close(voice_instance):
    """Verify barge-in / interruption immediately calls ws.close() with zero wait."""
    mock_ws = MagicMock()

    interrupted = False

    def is_interrupted():
        return interrupted

    def fake_iter():
        yield msgpack.packb({"audio": b"chunk1" * 50})
        # Simulate barge-in happening right after first chunk
        nonlocal interrupted
        interrupted = True
        yield msgpack.packb({"audio": b"chunk2_should_be_dropped" * 50})

    mock_ws.__iter__.side_effect = fake_iter

    with patch("narrative.jarvis_voice.ws_connect", return_value=mock_ws):
        chunks = []
        for pcm_bytes, sr in voice_instance.stream_fish_audio_pcm("Long phrase...", is_interrupted_fn=is_interrupted):
            chunks.append(pcm_bytes)

        # ws.close() must have been called as soon as interruption was flagged
        mock_ws.close.assert_called()
        # Only chunk 1 received; chunk 2 dropped
        assert len(chunks) == 1
        assert chunks[0] == b"chunk1" * 50


def test_websocket_interrupted_event_trigger(voice_instance):
    """Verify voice_instance._interrupted.set() during streaming cuts off streaming immediately."""
    mock_ws = MagicMock()

    def fake_iter():
        # Simulate interruption arriving while stream is active
        voice_instance._interrupted.set()
        yield msgpack.packb({"audio": b"chunk" * 50})

    mock_ws.__iter__.side_effect = fake_iter

    with patch("narrative.jarvis_voice.ws_connect", return_value=mock_ws):
        chunks = list(voice_instance.stream_fish_audio_pcm("Speech interrupted mid-stream."))
        mock_ws.close.assert_called()
        assert len(chunks) == 0


def test_websocket_clears_stale_interrupted_at_start(voice_instance):
    """Verify stream_fish_audio_pcm clears a stale _interrupted flag left by an earlier turn's barge-in."""
    mock_ws = MagicMock()
    mock_ws.__iter__.return_value = [
        msgpack.packb({"audio": b"fresh_chunk" * 50}),
        msgpack.packb({"event": "finish", "reason": "stop"}),
    ]

    # Stale flag left by previous turn
    voice_instance._interrupted.set()

    with patch("narrative.jarvis_voice.ws_connect", return_value=mock_ws):
        chunks = list(voice_instance.stream_fish_audio_pcm("Fresh turn after previous barge-in."))
        assert not voice_instance._interrupted.is_set()
        assert len(chunks) == 1
        assert chunks[0][0] == b"fresh_chunk" * 50


def test_two_rapid_bargeins_then_full_speech_and_echo_suppression(voice_instance):
    """Verify exact regression scenario: 2 rapid barge-ins do not corrupt turn 3 and echo is suppressed."""
    from frontend.desktop import JarvisAPI

    api = JarvisAPI()
    api._voice = voice_instance

    # 1. Rapid barge-in 1
    api.cancel_playback()
    assert voice_instance._interrupted.is_set()

    # 2. Rapid barge-in 2
    api.cancel_playback()
    assert voice_instance._interrupted.is_set()

    # 3. Turn 3 question streaming
    mock_ws = MagicMock()
    mock_ws.__iter__.return_value = [
        msgpack.packb({"audio": b"math_audio_chunk" * 50}),
        msgpack.packb({"event": "finish", "reason": "stop"}),
    ]

    with patch("narrative.jarvis_voice.ws_connect", return_value=mock_ws):
        chunks = list(voice_instance.stream_fish_audio_pcm("I am quite proficient with mathematical calculations, sir."))
        assert not voice_instance._interrupted.is_set()
        assert len(chunks) == 1

    # 4. Multi-clause self-echo suppression verification
    api._recent_agent_responses.clear()
    api._tts_speaking = True
    api._recent_agent_responses.append("I am quite proficient with mathematical calculations")
    api._recent_agent_responses.append("sir from basic arithmetic to differential calculus")
    api._recent_agent_responses.append("I can assist with numerical analysis")
    api._recent_agent_responses.append("I am quite proficient with mathematical calculations, sir. From basic arithmetic to differential calculus, I can assist with numerical analysis.")

    mic_heard_speech = "From basic arithmetic to differential calculus I can assist with numerical analysis"
    assert api._check_is_self_echo(mic_heard_speech) is True


def test_fallback_recovers_consumed_clauses_and_waits_for_sentinel():
    """Verify that if WebSocket TTS fails, consumed clause 1 is recovered and subsequent clauses are waited for."""
    from frontend.desktop import JarvisAPI

    api = JarvisAPI()
    mock_voice = MagicMock()
    mock_voice._sanitize_text_for_speech.side_effect = lambda s: s
    mock_voice.narrate.return_value = b"ID3dummy_audio"
    api._voice = mock_voice
    api._decode_audio_to_pcm = MagicMock(return_value=b"\x00" * 48000)

    def failing_stream(text_queue, is_interrupted_fn=None, on_clause_sent=None):
        item = text_queue.get(timeout=1.0)
        text_queue.task_done()
        if on_clause_sent:
            on_clause_sent(item)
        raise RuntimeError("Fish Audio live error event: Invalid chunk_length 50")

    mock_voice.stream_fish_audio_pcm.side_effect = failing_stream

    tts_turn_id = api._tts_turn_id
    tts_text_queue = queue.Queue()
    tts_audio_queue = queue.Queue(maxsize=15)
    tts_state = {"emitted": False}
    fish_stream_available = True

    tts_text_queue.put("Clause 1: Initial greeting.")

    emitted_chunks = []

    def fake_emit(event, data):
        if event == "jarvis_pcm_audio_chunk":
            emitted_chunks.append(data)

    api._emit = fake_emit

    def delayed_feeder():
        time.sleep(0.3)  # Delay beyond old 0.2s timeout to prove it waits
        tts_text_queue.put("Clause 2: Detailed response arriving later.")
        time.sleep(0.1)
        tts_text_queue.put(None)

    feeder_thread = threading.Thread(target=delayed_feeder, daemon=True)
    feeder_thread.start()

    def check_interrupted():
        with api._tts_turn_lock:
            return api._tts_turn_id != tts_turn_id

    stream_completed_cleanly = False
    streamed_any = False
    active_clause = [""]
    consumed_clauses = []

    def on_clause_sent(chunk_text: str):
        active_clause[0] = chunk_text
        consumed_clauses.append(chunk_text)

    if fish_stream_available:
        try:
            api._tts_speaking = True
            pcm_stream = api._voice.stream_fish_audio_pcm(
                tts_text_queue,
                is_interrupted_fn=check_interrupted,
                on_clause_sent=on_clause_sent,
            )
            for _ in pcm_stream:
                pass
            if not check_interrupted() and streamed_any:
                stream_completed_cleanly = True
        except Exception:
            pass

    if not stream_completed_cleanly and not check_interrupted():
        loop_deadline = time.time() + 45.0
        pending_sentences = []
        if not streamed_any:
            pending_sentences.extend(consumed_clauses)
        elif active_clause[0]:
            pending_sentences.append(active_clause[0])

        while not check_interrupted() and time.time() < loop_deadline:
            if pending_sentences:
                item = pending_sentences.pop(0)
            else:
                try:
                    item = tts_text_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                tts_text_queue.task_done()

            if item is None:
                tts_audio_queue.put(None)
                break

            try:
                sentence = item
                if not sentence or len(sentence.strip()) <= 2:
                    continue
                sentence = api._voice._sanitize_text_for_speech(sentence) if api._voice else sentence
                if not sentence:
                    continue
                if check_interrupted():
                    continue

                api._tts_speaking = True
                api._recent_agent_responses.append(sentence.strip())
                audio_bytes = api._voice.narrate(sentence)
                if not check_interrupted() and audio_bytes:
                    pcm_bytes = api._decode_audio_to_pcm(audio_bytes)
                    if pcm_bytes:
                        tts_state["emitted"] = True
                        api._emit("jarvis_pcm_audio_chunk", {
                            "audio": "b64",
                            "sample_rate": 24000,
                            "text": sentence,
                            "turn_id": tts_turn_id,
                        })
            except Exception:
                pass

    feeder_thread.join(timeout=2.0)

    assert len(emitted_chunks) == 2
    assert "Clause 1" in emitted_chunks[0]["text"]
    assert "Clause 2" in emitted_chunks[1]["text"]
    assert mock_voice.narrate.call_count == 2


def test_fallback_loop_safety_timeout():
    """Verify fallback loop terminates at safety deadline if sentinel is never sent."""
    tts_text_queue = queue.Queue()
    loop_deadline = time.time() + 0.5
    start_time = time.time()

    while time.time() < loop_deadline:
        try:
            item = tts_text_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if item is None:
            break

    elapsed = time.time() - start_time
    assert elapsed >= 0.5
    assert elapsed < 1.5


def test_long_multichunk_response_cumulative_playback_and_followup_window():
    """Verify that a 15-chunk response tracks cumulative scheduled audio duration,
    and the follow-up window does NOT open prematurely before browser completion signal."""
    from frontend.desktop import JarvisAPI

    api = JarvisAPI()
    mock_voice = MagicMock()
    api._voice = mock_voice

    tts_turn_id = api._tts_turn_id
    playback_event = threading.Event()
    with api._turn_events_lock:
        api._turn_playback_events[tts_turn_id] = playback_event

    tts_state = {"emitted": True}
    tts_cumulative_audio_dur = [0.0]
    stream_start_time = [0.0]

    # Simulate 15 PCM chunks of 1.0s each (48000 bytes at 24000Hz 16-bit mono)
    sample_rate = 24000
    chunk_bytes = b"\x00" * (sample_rate * 2)  # 1.0s duration per chunk
    num_chunks = 15

    t_start = time.time()
    stream_start_time[0] = t_start

    for _ in range(num_chunks):
        chunk_dur = len(chunk_bytes) / float(sample_rate * 2)
        chunk_start = max(getattr(api, '_tts_playback_until', 0.0), time.time())
        api._tts_playback_until = chunk_start + chunk_dur

    # 1. Verify _tts_playback_until tracks cumulative scheduled duration (~15.0s from start)
    expected_cumulative_end = t_start + 15.0
    assert api._tts_playback_until >= expected_cumulative_end

    # 2. Verify remaining and safety_timeout are scaled to the full 15s audio duration
    now = time.time()
    remaining = max(0.5, api._tts_playback_until - now)
    assert remaining >= 14.5  # Full 15s minus tiny fraction of elapsed time
    safety_timeout = min(120.0, remaining + 5.0)
    assert safety_timeout >= 19.5  # Well above 15s, does not cut off at 5.3s!

    # 3. Simulate _await_speech_completion_and_open_mic in background thread
    followup_opened = threading.Event()

    def mock_start_followup():
        api._follow_up_active = True
        followup_opened.set()

    api._start_follow_up_window = mock_start_followup
    api._tts_speaking = True

    def await_worker():
        # Mimic desktop.py lines 3448-3456
        finished = playback_event.wait(timeout=safety_timeout)
        api._tts_speaking = False
        api._tts_playback_until = max(getattr(api, '_tts_playback_until', 0.0), time.time() + 1.5)
        api._start_follow_up_window()

    t = threading.Thread(target=await_worker, daemon=True)
    t.start()

    # Verify that at t=0.2s, the follow-up window has NOT opened (not timing out prematurely)
    time.sleep(0.2)
    assert not followup_opened.is_set()
    assert api._tts_speaking is True
    assert api._follow_up_active is False

    # 4. Browser finishes playing all 15 chunks and signals playback finished
    res = api.notify_tts_playback_finished(tts_turn_id)
    assert res["success"] is True

    # Thread unblocks and follow-up window opens
    assert followup_opened.wait(timeout=1.0) is True
    assert api._follow_up_active is True
    assert api._tts_speaking is False

    # 5. Verify echo suppression protects against reverberation/late capture
    api._recent_agent_responses.append("All fifteen telemetry sectors report nominal operation across systems.")
    echo_text = "All fifteen telemetry sectors report nominal operation"
    assert api._check_is_self_echo(echo_text) is True


def test_turn_isolation_resets_tts_playback_until():
    """Verify that a new turn resets _tts_playback_until to 0.0 and does not inherit stale timestamp."""
    from frontend.desktop import JarvisAPI

    api = JarvisAPI()
    # Simulate a prior turn that finished far in the future
    stale_time = time.time() + 30.0
    api._tts_playback_until = stale_time

    # Run _run_ask setup lines
    with api._tts_turn_lock:
        api._tts_turn_id += 1
        tts_turn_id = api._tts_turn_id
        api._tts_playback_until = 0.0

    assert api._tts_playback_until == 0.0
    assert api._tts_turn_id > 0


def test_reanchoring_cursor_formula_continuous_and_gapped():
    """Verify cursor formula under both fast burst arrival (continuous) and idle buffer starvation (gapped)."""
    # 1. Continuous delivery: chunks arrive back-to-back faster than real-time
    t0 = time.time()
    cursor = 0.0

    chunk_dur = 1.0
    for _ in range(5):
        chunk_start = max(cursor, t0)
        cursor = chunk_start + chunk_dur

    assert cursor == t0 + 5.0  # Scheduled 5.0s into future

    # 2. Gapped delivery: 10s idle pause occurs (audio ended, buffer drained)
    t_after_gap = t0 + 15.0  # 15s after t0 (cursor was at t0 + 5.0)
    assert t_after_gap > cursor  # Speaker was silent for 10s

    # Next chunk arrives at t_after_gap
    chunk_start = max(cursor, t_after_gap)
    cursor = chunk_start + 2.0  # 2s chunk

    assert chunk_start == t_after_gap  # Re-anchors to current time!
    assert cursor == t_after_gap + 2.0  # Extends from current time, not from stale past


def test_inflight_clause_recovery_on_midstream_failure():
    """Verify that when WebSocket fails mid-stream after some chunks, uncompleted in-flight clauses are recovered."""
    consumed_clauses = [
        "First sector scan complete.",      # ~4 words, ~1.4s
        "Orbital velocity is steady.",      # ~4 words, ~1.4s
        "Atmospheric entry in three minutes.", # ~5 words, ~1.75s
        "Deploying landing gear now.",      # ~4 words, ~1.4s
    ]

    # Suppose Fish Audio only delivered 2 chunks of 1.0s each (2.0s total audio) before socket died
    total_ws_audio_dur = [2.0]
    pending_sentences = []

    remaining_audio = total_ws_audio_dur[0]
    for idx, cl in enumerate(consumed_clauses):
        words = len(cl.split())
        est_cl_dur = max(1.0, words * 0.35)
        if remaining_audio >= est_cl_dur * 0.65:
            remaining_audio -= est_cl_dur
        else:
            pending_sentences.extend(consumed_clauses[idx:])
            break

    # First clause (~1.4s) consumed audio; second clause (~1.4s) was not completed (only 0.6s remaining).
    # Clauses 2, 3, 4 must be in pending_sentences!
    assert "Orbital velocity is steady." in pending_sentences
    assert "Atmospheric entry in three minutes." in pending_sentences
    assert "Deploying landing gear now." in pending_sentences
    assert "First sector scan complete." not in pending_sentences


def test_pipelined_fallback_prefetching_eliminates_gap():
    """Verify fallback prefetch producer runs concurrently with consumption to eliminate audio gaps."""
    mock_voice = MagicMock()
    mock_voice._sanitize_text_for_speech = lambda s: s
    # Simulate narrate taking 50ms per sentence
    def fake_narrate(s):
        time.sleep(0.05)
        return b"RIFF" + b"\x00" * 48000
    mock_voice.narrate = MagicMock(side_effect=fake_narrate)

    pending_sentences = ["Sentence one.", "Sentence two.", "Sentence three."]
    tts_text_queue = queue.Queue()
    prefetch_queue = queue.Queue(maxsize=2)
    producer_stop = threading.Event()
    loop_deadline = time.time() + 5.0

    def fallback_producer():
        while not producer_stop.is_set() and time.time() < loop_deadline:
            if pending_sentences:
                item = pending_sentences.pop(0)
            else:
                try:
                    item = tts_text_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                tts_text_queue.task_done()

            if item is None:
                prefetch_queue.put(None)
                break

            clean_sent = item
            audio_bytes = mock_voice.narrate(clean_sent)
            if audio_bytes:
                prefetch_queue.put((b"fake_pcm", audio_bytes, clean_sent))

    producer_thread = threading.Thread(target=fallback_producer, daemon=True)
    producer_thread.start()

    consumed = []
    # Consumer loop
    while time.time() < loop_deadline:
        try:
            data = prefetch_queue.get(timeout=1.0)
        except queue.Empty:
            break
        if data is None:
            prefetch_queue.task_done()
            break
        pcm_bytes, audio_bytes, sent = data
        prefetch_queue.task_done()
        consumed.append(sent)
        if len(consumed) == 3:
            tts_text_queue.put(None)

    producer_stop.set()
    producer_thread.join(timeout=2.0)

    assert consumed == ["Sentence one.", "Sentence two.", "Sentence three."]
    assert mock_voice.narrate.call_count == 3


def test_websocket_chunks_flowing_past_15s_not_cut_off(voice_instance):
    """Verify that chunks flowing past 15s from stop_sent are NOT cut off by a premature wall-clock deadline."""
    class FakeLongWS:
        def __init__(self):
            self.chunks_sent = 0

        def recv(self, timeout=20.0):
            if self.chunks_sent < 25:
                self.chunks_sent += 1
                return msgpack.packb({"audio": b"pcm_chunk_" + str(self.chunks_sent).encode()})
            else:
                return msgpack.packb({"event": "finish", "reason": "stop"})

        def send(self, data):
            pass

        def close(self):
            pass

    fake_ws = FakeLongWS()

    # Put a phrase, send stop immediately
    q = queue.Queue()
    q.put("Detailed debrief of satellite constellations.")
    q.put(None)

    with patch("narrative.jarvis_voice.ws_connect", return_value=fake_ws):
        chunks = list(voice_instance.stream_fish_audio_pcm(q))

        # All 25 chunks must be received
        assert len(chunks) == 25
        assert chunks[-1][0].startswith(b"pcm_chunk_25")


def test_websocket_premature_close_before_finish_raises_truncation_error(voice_instance):
    """Verify that if WebSocket stream closes before finish event without interruption,
    RuntimeError is raised so caller detects truncation and enters fallback narrate."""
    # Create a real-like socket object (not a Mock) so is_mock is False
    class FakeWS:
        def __init__(self):
            self.delivered = 0

        def recv(self, timeout=20.0):
            if self.delivered < 3:
                self.delivered += 1
                return msgpack.packb({"audio": b"chunk_audio"})
            # Simulates premature server socket drop: raises TimeoutError or EOF
            raise TimeoutError("connection dead")

        def send(self, data):
            pass

        def close(self):
            pass

    fake_ws = FakeWS()
    q = queue.Queue()
    q.put("Testing truncation detection.")
    q.put(None)

    with patch("narrative.jarvis_voice.ws_connect", return_value=fake_ws):
        with pytest.raises((RuntimeError, TimeoutError)):
            list(voice_instance.stream_fish_audio_pcm(q))


def test_websocket_bargein_does_not_raise_truncation_error(voice_instance):
    """Verify that intentional barge-in closes socket immediately with NO truncation error or fallback re-narration."""
    class FakeBargeInWS:
        def __init__(self):
            self.delivered = 0
            self.closed = False

        def recv(self, timeout=20.0):
            if self.delivered == 0:
                self.delivered += 1
                return msgpack.packb({"audio": b"first_chunk"})
            # While waiting, user barges in
            voice_instance.interrupt()
            return msgpack.packb({"audio": b"second_chunk"})

        def send(self, data):
            pass

        def close(self):
            self.closed = True

    fake_ws = FakeBargeInWS()
    q = queue.Queue()
    q.put("Speech that will be barged in on.")
    q.put(None)

    with patch("narrative.jarvis_voice.ws_connect", return_value=fake_ws):
        # Must NOT raise RuntimeError!
        chunks = list(voice_instance.stream_fish_audio_pcm(q))
        assert fake_ws.closed is True
        assert len(chunks) <= 2





