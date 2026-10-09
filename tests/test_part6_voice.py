"""
Tests for Part 6: Voice and Wake-Word Engine Improvements.
Verifies fuzzy matching for wake word variants (J.A.R.C., Jarvik, Jarvis, Jarbis),
Whisper VAD tuning, unprimed bias prompt, and preservation of wake word utterances.
"""
import inspect
from core.wake_word import WakeWordEngine, _levenshtein_distance, _levenshtein_ratio
from frontend.desktop import JarvisAPI


def test_levenshtein_distance_and_ratio():
    """Verify pure Python edit distance and normalized similarity calculations."""
    assert _levenshtein_distance("jarvis", "jarvis") == 0
    assert _levenshtein_ratio("jarvis", "jarvis") == 1.0

    # 1 edit
    assert _levenshtein_distance("jarvis", "jarvik") == 1
    assert _levenshtein_ratio("jarvis", "jarvik") >= 0.75

    assert _levenshtein_distance("jarvis", "jarbis") == 1
    assert _levenshtein_ratio("jarvis", "jarbis") >= 0.75

    # Completely different
    assert _levenshtein_distance("jarvis", "weather") > 2
    assert _levenshtein_ratio("jarvis", "weather") < 0.50


def test_fuzzy_wake_word_variants():
    """Verify check_stt_text_for_wake_or_aliases matches J.A.R.C., Jarvik, Jarvis, and Jarbis."""
    engine = WakeWordEngine()

    variants = [
        # Direct variants
        "Jarvis, what is the status?",
        "J.A.R.C., what is the status?",
        "Jarc, what is the status?",
        "J. A. R. C. what is the status?",
        "Jarvik what is the weather",
        "Jarbis check this",
        # Prefixed variants
        "Hey J.A.R.C.",
        "Hey Jarvik",
        "Hey Jarbis",
        "Hey Jarvis",
        "Wake up Jarvik",
        "Wake up J.A.R.C.",
        "Alright Jarbis",
        "Yo Jarvik",
        # Phonetic variants
        "Jervis what is the time",
        "Hey Javis look here",
    ]

    for utterance in variants:
        matched, phrase = engine.check_stt_text_for_wake_or_aliases(utterance)
        assert matched is True, f"Failed to match wake variant for: '{utterance}'"
        assert phrase != "", f"Matched phrase was empty for: '{utterance}'"


def test_ambient_speech_rejection():
    """Verify wake engine does not false trigger on ambient speech."""
    engine = WakeWordEngine()
    ambient_utterances = [
        "the weather in Tokyo is really pleasant today",
        "my friend called me on the phone earlier",
        "order some extra cheese pizza for lunch",
        "we should probably commit these changes to git",
        "can someone turn off the living room lights",
        "the stock market dropped two percent this morning",
        "park the car over there",
        "part of the machine broke down",
    ]
    for utterance in ambient_utterances:
        matched, phrase = engine.check_stt_text_for_wake_or_aliases(utterance)
        assert matched is False, f"False positive trigger on: '{utterance}' (matched '{phrase}')"


def test_whisper_bias_prompt_no_wake_priming():
    """Verify Whisper bias prompt does not prime with J.A.R.V.I.S. wake word to prevent silence hallucinations."""
    src = inspect.getsource(JarvisAPI._transcribe_audio_fast)
    # The bias_prompt line must not begin with or prime with J.A.R.V.I.S.
    assert "bias_prompt = \"Tactical intelligence" in src
    assert "bias_prompt = \"J.A.R.V.I.S." not in src


def test_whisper_preserves_wake_word_text():
    """Verify _sanitize_transcribed_speech does NOT discard spoken wake words."""
    # Genuine wake words should not be cleared to empty string
    res_jarvis = JarvisAPI._sanitize_transcribed_speech("JARVIS")
    assert res_jarvis == "JARVIS"

    res_hey = JarvisAPI._sanitize_transcribed_speech("Hey Jarvis")
    assert res_hey == "Hey Jarvis"

    # Hallucination subtitles / artifacts should still be cleared
    res_sub = JarvisAPI._sanitize_transcribed_speech("Subtitles by Community")
    assert res_sub == ""


def test_whisper_vad_and_probability_thresholds():
    """Verify offline Whisper uses vad_filter and probability/logprob thresholds."""
    src = inspect.getsource(JarvisAPI._transcribe_audio_offline_whisper)
    assert "vad_filter=True" in src
    assert "no_speech_threshold=0.6" in src
    assert "no_speech_prob" in src
    assert "avg_logprob" in src
