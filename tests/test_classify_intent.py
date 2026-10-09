import pytest
from unittest.mock import MagicMock, patch
from narrative.jarvis_voice import JarvisVoice


def test_classify_intent_passes_classification_prompt_to_cloud():
    """Verify that classify_intent passes the full classification prompt (not raw text) to cloud models."""
    voice = JarvisVoice()

    captured_prompt = None
    captured_system = None

    def mock_ask_cloud(prompt, system, max_tokens=150, on_token=None, image_path=None):
        nonlocal captured_prompt, captured_system
        captured_prompt = prompt
        captured_system = system
        return {"text": '{"type": "investigate", "target": "target.com"}', "rate_limited": False, "engine": "groq"}

    with patch.object(voice, "_ask_cloud", side_effect=mock_ask_cloud):
        res = voice.classify_intent("Please inspect target target.com for open vulnerabilities")
        assert res["type"] == "investigate"
        assert res["target"] == "target.com"

        # Verify the classification prompt contains the rules and schema
        assert captured_prompt is not None
        assert "Analyze this user message and determine if it is an OSINT investigation request" in captured_prompt
        assert '{"type": "investigate" or "covo"' in captured_prompt
        assert 'User message: "Please inspect target target.com for open vulnerabilities"' in captured_prompt
        assert captured_system == "You are a precise intent classification agent. Output raw JSON only."


def test_classify_intent_covo_classification():
    """Verify that conversational questions with investigation keywords route to covo when cloud classifies them as covo."""
    voice = JarvisVoice()

    with patch.object(voice, "_ask_cloud", return_value={"text": '{"type": "covo", "target": null}', "rate_limited": False, "engine": "nvidia"}):
        res = voice.classify_intent("How do penetration testers investigate target infrastructure?")
        assert res["type"] == "covo"
        assert res["target"] is None


def test_classify_intent_slm_fallback_receives_prompt():
    """Verify that when cloud returns empty or rate-limited, local SLM fallback receives the classification prompt."""
    voice = JarvisVoice()

    captured_slm_prompt = None

    def mock_ask_slm(prompt, system, max_tokens=150, timeout=30):
        nonlocal captured_slm_prompt
        captured_slm_prompt = prompt
        return {"text": '{"type": "investigate", "target": "192.168.1.1"}'}

    with patch.object(voice, "_ask_cloud", return_value={"text": "", "rate_limited": True, "engine": None}):
        with patch.object(voice, "_ask_slm", side_effect=mock_ask_slm):
            res = voice.classify_intent("Could you trace target 192.168.1.1?")
            assert res["type"] == "investigate"
            assert res["target"] == "192.168.1.1"
            assert captured_slm_prompt is not None
            assert "Analyze this user message" in captured_slm_prompt
