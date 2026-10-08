import pytest
import re
from core.agent_router import AgentRouter
from core.task import TaskType

def test_conversational_queries_not_hijacked_as_search():
    router = AgentRouter()
    
    # Conversational questions that should NOT be hijacked into GOOGLE_SEARCH
    conversational_inputs = [
        "Tell me about Coimbatore",
        "Who is Alan Turing?",
        "Tell me a fun fact about this",
        "Find a way to solve this math problem",
        "What can you find in our internal database?",
        "Jarvis is actually not a city in India"
    ]
    
    for text in conversational_inputs:
        handled, msg, task, cat = router.route_input(text)
        if handled:
            assert cat != "google_search", f"'{text}' was mistakenly routed to google_search!"
            if task:
                assert task.type != TaskType.GOOGLE_SEARCH.value, f"'{text}' generated a GOOGLE_SEARCH task!"

def test_explicit_search_commands_routed_properly():
    router = AgentRouter()
    
    explicit_inputs = [
        ("google search for quantum computing", "quantum computing"),
        ("search google for latest nvidia stock", "latest nvidia stock"),
        ("search the web for python 3.14 features", "python 3.14 features"),
        ("web search artificial intelligence", "artificial intelligence")
    ]
    
    for text, expected_query in explicit_inputs:
        handled, msg, task, cat = router.route_input(text)
        assert handled is True, f"'{text}' was not handled"
        assert cat == "google_search", f"'{text}' did not have cat google_search"
        assert task is not None
        assert task.type == TaskType.GOOGLE_SEARCH.value
        assert expected_query.lower() in task.data["query"].lower()

def test_cognitive_search_directive_stripping():
    raw = "Certainly, Sir. [SEARCH: live weather Coimbatore] Checking regional telemetry."
    m = re.search(r'\[\s*SEARCH\s*:\s*([^\]]+)\]', raw)
    assert m is not None
    assert m.group(1).strip() == "live weather Coimbatore"
    
    clean_text = re.sub(r'\[\s*(?:NAV|LAYER|CMD|ZOOM|RADIO|SFX|ANNOTATE|COCKPIT|SEARCH)[^\]]*\]', '', raw)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    assert clean_text == "Certainly, Sir. Checking regional telemetry."

def test_normalize_wav_audio_with_hpf(tmp_path):
    import wave
    import struct
    from frontend.desktop import JarvisAPI
    
    test_wav = str(tmp_path / "test_audio.wav")
    # Generate 16kHz mono audio with 50Hz hum and speech signal
    samples = []
    for i in range(16000):
        # 50Hz hum (amplitude 1000) + speech signal (amplitude 3000)
        import math
        s = int(1000 * math.sin(2 * math.pi * 50 * i / 16000) + 3000 * math.sin(2 * math.pi * 500 * i / 16000))
        samples.append(s)
    
    with wave.open(test_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        
    res = JarvisAPI.normalize_wav_audio(test_wav)
    assert res is True
    
    with wave.open(test_wav, "rb") as wf:
        raw = wf.readframes(wf.getnframes())
        boosted = struct.unpack(f"<{len(raw)//2}h", raw)
        max_boosted = max(abs(b) for b in boosted)
        assert max_boosted > 10000

def test_incomplete_connector_regex():
    incomplete_connectors = r'\b(?:a|an|the|not|and|or|is|are|to|about|like|for|with|in|at|of|actually|not\s+a)\s*$'
    
    assert re.search(incomplete_connectors, "J.A.R.V.I.S. is actually not a") is not None
    assert re.search(incomplete_connectors, "Tell me about") is not None
    assert re.search(incomplete_connectors, "Go to") is not None
    assert re.search(incomplete_connectors, "The weather in") is not None
    
    # Complete sentences should not match
    assert re.search(incomplete_connectors, "J.A.R.V.I.S. is in Coimbatore") is None
    assert re.search(incomplete_connectors, "Take me to Chennai") is None
    assert re.search(incomplete_connectors, "What time is it") is None

