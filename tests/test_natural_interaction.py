# tests/test_natural_interaction.py
"""
Unit tests for Part 1 (Multi Wake-word & Continued-Conversation Mode)
and Part 2 (Rolling Short-Term Context, Intent Classification & Slot Clarification).
"""

import time
import pytest
from core.wake_word import WakeWordEngine, _levenshtein_ratio, _phonetic_code
from core.conversation_context import ConversationContextManager, ConversationTurn


class TestMultiWakeWord:
    def setup_method(self):
        self.phrases = [
            "jarvis", "hey jarvis", "jarvis you there", "wake up jarvis", "alright jarvis", "yo jarvis"
        ]
        self.engine = WakeWordEngine(wake_phrases=self.phrases)

    def test_exact_and_prefix_matches(self):
        cases = [
            ("Jarvis, what is the status?", True, "Jarvis"),
            ("Hey Jarvis run a scan", True, "Hey Jarvis"),
            ("jarvis you there please report", True, "Jarvis You There"),
            ("wake up jarvis", True, "Wake Up Jarvis"),
            ("alright jarvis", True, "Alright Jarvis"),
            ("yo jarvis, check air traffic", True, "Yo Jarvis"),
        ]
        for text, expected_match, expected_phrase in cases:
            matched, phrase = self.engine.check_stt_text_for_wake_or_aliases(text)
            assert matched == expected_match, f"Failed for '{text}'"
            assert phrase.lower() == expected_phrase.lower(), f"Expected '{expected_phrase}', got '{phrase}'"

    def test_local_phonetic_and_fuzzy_matches(self):
        cases = [
            ("Jervis what is the weather", True, "Jarvis"),
            ("Hey Javis look at this", True, "Hey Jarvis"),
            ("Alright Jervis", True, "Alright Jarvis"),
        ]
        for text, expected_match, expected_phrase in cases:
            matched, phrase = self.engine.check_stt_text_for_wake_or_aliases(text)
            assert matched == expected_match, f"Failed fuzzy match for '{text}'"

    def test_ambient_speech_rejection(self):
        ambient_utterances = [
            "the weather in Tokyo is really pleasant today",
            "my friend called me on the phone earlier",
            "order some extra cheese pizza for lunch",
            "we should probably commit these changes to git",
            "can someone turn off the living room lights",
            "the stock market dropped two percent this morning"
        ]
        for text in ambient_utterances:
            matched, phrase = self.engine.check_stt_text_for_wake_or_aliases(text)
            assert not matched, f"False positive trigger on ambient speech: '{text}' (matched '{phrase}')"


class TestContinuedConversationMode:
    def test_follow_up_window_lifecycle(self):
        engine = WakeWordEngine()
        assert not engine.is_in_follow_up()

        # Start follow-up window
        engine.start_follow_up_window(duration_sec=5.0)
        assert engine.is_in_follow_up()
        assert engine.is_window_active

        # Cancel follow-up window
        engine.cancel_follow_up_window()
        assert not engine.is_in_follow_up()
        assert not engine.is_window_active


class TestConversationContextManager:
    def setup_method(self):
        self.mgr = ConversationContextManager(max_turns=6)

    def test_new_command_fast_path(self):
        c, intent, entities, res = self.mgr.classify_and_resolve("go to Paris")
        assert c == "new_command"
        assert intent == "geo_nav"
        assert entities.get("location") == "paris"

        c2, intent2, entities2, res2 = self.mgr.classify_and_resolve("investigate elonmusk")
        assert c2 == "new_command"
        assert intent2 == "investigate"
        assert entities2.get("target") == "elonmusk"

    def test_follow_up_reference_resolution(self):
        # Turn 1
        c1, i1, e1, r1 = self.mgr.classify_and_resolve("investigate billgates")
        self.mgr.record_turn("investigate billgates", c1, i1, e1, r1, "Investigating billgates...")

        # Turn 2: Follow-up pronoun reference
        c2, i2, e2, r2 = self.mgr.classify_and_resolve("what about his twitter")
        assert c2 == "follow_up"
        assert i2 == "investigate"
        assert e2.get("target") == "billgates"
        assert e2.get("platform") == "twitter"
        assert "investigate billgates on twitter" in r2

    def test_missing_slot_clarification_flow(self):
        # Turn 1: Ambiguous command missing target slot
        c1, i1, e1, r1 = self.mgr.classify_and_resolve("start an investigation")
        assert c1 == "new_command"
        assert i1 == "investigate"
        pending = self.mgr.get_active_pending_slot()
        assert pending is not None
        assert pending["slot"] == "target"

        # Turn 2: Clarification answer fills slot immediately
        c2, i2, e2, r2 = self.mgr.classify_and_resolve("openai.com")
        assert c2 == "clarification_answer"
        assert i2 == "investigate"
        assert e2.get("target") == "openai.com"
        assert r2 == "investigate openai.com"
        assert self.mgr.get_active_pending_slot() is None

    def test_tactical_cockpit_and_lock_intents(self):
        # 1. Cockpit chase
        c1, i1, e1, r1 = self.mgr.classify_and_resolve("chase VIPER-11")
        assert c1 == "new_command"
        assert i1 == "cockpit_chase"
        assert e1.get("target") == "VIPER-11"

        # 2. Target lock
        c2, i2, e2, r2 = self.mgr.classify_and_resolve("lock target SKY-42")
        assert c2 == "new_command"
        assert i2 == "target_lock"
        assert e2.get("target") == "SKY-42"

        # 3. Release lock
        c3, i3, e3, r3 = self.mgr.classify_and_resolve("release lock")
        assert c3 == "new_command"
        assert i3 == "target_unlock"

    def test_file_pronoun_resolution_safety(self):
        from core.system_commander import get_system_commander
        cmdr = get_system_commander()
        cmdr.last_affected_file = "/home/joe/notebooks/Today_2026-09-24.md"

        # Seed previous turn so context manager can resolve follow-up reference
        self.mgr.record_turn("create a note for today", "new_command", "file_operation", {}, "create a note for today", "Created note.")

        # Explicit file rename command SHOULD resolve
        c1, i1, e1, r1 = self.mgr.classify_and_resolve("Actually rename this one to WiFi_Notes.md")
        assert i1 == "file_operation"
        assert "/home/joe/notebooks/Today_2026-09-24.md" in r1

        # General question containing 'this' or 'it' MUST NOT be corrupted into a file path
        c2, i2, e2, r2 = self.mgr.classify_and_resolve("how this hidden SSID has been discovered?")
        assert "/home/joe/notebooks" not in r2
        assert "hidden SSID" in r2

        # Memory complaint query MUST NOT be converted to file operation
        c3, i3, e3, r3 = self.mgr.classify_and_resolve("I didn't tell you to save it as a personal memory, remove it.")
        assert i3 != "file_operation"
        assert "/home/joe/notebooks" not in r3


class TestAgentRouterMemoryGating:
    def test_informational_queries_never_trigger_memory_store(self):
        from core.agent_router import AgentRouter
        router = AgentRouter()

        queries = [
            "what is the meaning of SSID?",
            "explain the meaning of deauthentication frames",
            "S.I.D.A. has been hidden, I mean, clocked, so we use passive method",
            "how this hidden SSID has been discovered?",
            "what does Wi-Fi deauth mean?",
            "tell me what this means",
        ]
        for q in queries:
            matched, msg, task, action = router.route_input(q)
            assert action != "memory_store", f"Query incorrectly triggered memory_store: '{q}'"

    def test_explicit_rule_storage_command(self):
        from core.agent_router import AgentRouter
        router = AgentRouter()

        explicit_rules = [
            "remember that my call sign is Ghost",
            "save this rule: always confirm before deleting files",
            "from now on always verify sha256 checksums",
        ]
        for r in explicit_rules:
            matched, msg, task, action = router.route_input(r)
            assert action == "memory_store", f"Explicit rule command failed to trigger memory_store: '{r}'"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
