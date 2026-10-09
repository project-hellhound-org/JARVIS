# tests/test_adaptive_salutation_and_upgrades.py
"""
Unit tests for:
1. Adaptive Operator Salutation ("Sir" vs "Ma'am" / "Madam")
2. Case Cross-Verification Intent Routing
3. Tactical Area Annotation Intent Routing
4. Reasoning Loop Monologue Persona Adaptability
"""

import pytest
from core.jarvis_memory import JarvisMemory
from core.agent_router import AgentRouter
from core.jarvis_reasoning_loop import JarvisCognitiveLoop


class TestAdaptiveSalutationAndUpgrades:
    def setup_method(self):
        self.memory = JarvisMemory()
        # Reset to default Sir before each test
        self.memory.set_salutation("Sir")
        self.router = AgentRouter()

    def teardown_method(self):
        # Reset back to Sir
        self.memory.set_salutation("Sir")

    def test_jarvis_memory_salutation_persistence(self):
        assert self.memory.get_salutation() == "Sir"
        
        # Change to Ma'am
        self.memory.set_salutation("Ma'am")
        assert self.memory.get_salutation() == "Ma'am"

        # Change to Madam
        self.memory.set_salutation("Madam")
        assert self.memory.get_salutation() == "Madam"

    def test_router_salutation_intent_maam(self):
        handled, msg, task, intent = self.router.route_input("call me ma'am")
        assert handled is True
        assert intent == "set_operator_salutation"
        assert "Ma'am" in msg
        assert self.memory.get_salutation() == "Ma'am"

    def test_router_salutation_intent_madam(self):
        handled, msg, task, intent = self.router.route_input("address me as madam from now on")
        assert handled is True
        assert intent == "set_operator_salutation"
        assert "Madam" in msg
        assert self.memory.get_salutation() == "Madam"

    def test_router_acknowledgment_uses_adapted_salutation(self):
        # Set to Ma'am
        self.memory.set_salutation("Ma'am")
        
        # Test terminal command
        handled, msg, task, intent = self.router.route_input("system diagnostic")
        assert handled is True
        assert "Ma'am" in msg
        assert "Sir" not in msg

    def test_router_cross_verify_intent(self):
        handled, msg, task, intent = self.router.route_input("cross verify this case")
        assert handled is True
        assert intent == "cross_verify_case"
        assert task is not None
        assert task.type == "cross_verify_case"
        assert "Cross-verifying active case telemetry" in msg

    def test_router_annotate_area_intent(self):
        handled, msg, task, intent = self.router.route_input("annotate this area")
        assert handled is True
        assert intent == "annotate_area"
        assert task is not None
        assert task.type == "annotate_area"
        assert "Illuminating tactical perimeter" in msg

    def test_router_annotate_named_sector_intent(self):
        handled, msg, task, intent = self.router.route_input("annotate sector Delta Zone")
        assert handled is True
        assert intent == "annotate_area"
        assert task is not None
        assert "Delta Zone" in task.data.get("sector_name", "")

    def test_reasoning_loop_adapts_to_maam(self):
        self.memory.set_salutation("Ma'am")
        loop = JarvisCognitiveLoop()
        assert loop.get_salutation() == "Ma'am"

        plan = loop.analyze_goal("check air traffic")
        assert len(plan) > 0
        first_step = plan[0]
        # Spoken progress phrases should use Ma'am
        assert "Ma'am" in first_step.get("progress_phrase", "")
        assert "Sir" not in first_step.get("progress_phrase", "")

    def test_router_salutation_custom_and_leet_handles(self):
        # 1. Custom "mam"
        handled, msg, task, intent = self.router.route_input("yo jarvis call me mam")
        assert handled is True
        assert intent == "set_operator_salutation"
        assert "Mam" in msg
        assert self.memory.get_salutation() == "Mam"

        # 2. Custom "Boss"
        handled, msg, task, intent = self.router.route_input("call me boss")
        assert handled is True
        assert intent == "set_operator_salutation"
        assert "Boss" in msg
        assert self.memory.get_salutation() == "Boss"

        # 3. Custom handle in leet "l4zz3rj0d"
        handled, msg, task, intent = self.router.route_input("call me l4zz3rj0d")
        assert handled is True
        assert intent == "set_operator_salutation"
        assert "l4zz3rj0d" in msg
        assert self.memory.get_salutation() == "l4zz3rj0d"

    def test_creator_provenance_with_active_salutation(self):
        self.memory.set_salutation("Mam")
        handled, msg, task, intent = self.router.route_input("who built you")
        assert handled is True
        assert intent == "creator_provenance"
        assert "Project Hellhound" in msg
        assert "l4zz3rj0d" in msg
        assert "Mam" in msg

        # With handle
        self.memory.set_salutation("l4zz3rj0d")
        handled, msg, task, intent = self.router.route_input("who is your creator")
        assert handled is True
        assert "l4zz3rj0d" in msg

    def test_leet_speak_speech_sanitization(self):
        from narrative.jarvis_voice import JarvisVoice
        voice = JarvisVoice()
        
        # Test creator name phonetic normalization
        creator_text = "I was engineered and deployed by Project Hellhound, created by l4zz3rj0d."
        clean = voice._sanitize_text_for_speech(creator_text)
        assert "lazzerjod" in clean
        assert "l4zz3rj0d" not in clean

        # Test general leet speak conversion
        leet_text = "Hello b0ss, the pr0ject h3llh0und h4ck3r sk1ll is 1337."
        clean_leet = voice._sanitize_text_for_speech(leet_text)
        assert "boss" in clean_leet
        assert "project" in clean_leet
        assert "hellhound" in clean_leet
        assert "hacker" in clean_leet
        assert "skill" in clean_leet
        assert "leet" in clean_leet
