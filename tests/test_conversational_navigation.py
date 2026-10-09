"""
Unit tests for Part 2: Questions Must Not Navigate or Toggle Layers.
Verifies:
1. Queries like 'what model are we using', 'how do you work', 'who made you',
   'I am fine J.A.R.V.I.S., but what model are we using for you?', or any
   conversational question produce 0 navigation steps and 0 layer changes.
2. Strict verb requirements for navigation: camera only glides/flies when
   the user explicitly commands it ('fly to', 'go to', 'take me to', etc.).
3. Strict action verb requirements for tactical layers: toggling airspace,
   maritime, satellites requires explicit action verbs ('show', 'turn on', etc.).
4. Over 20 negative conversational sentences that must NOT navigate or toggle layers,
   plus positive tests that explicit commands still do navigate.
"""

import pytest
from unittest.mock import MagicMock, patch

from core.jarvis_reasoning_loop import JarvisCognitiveLoop
from frontend.desktop import JarvisAPI


NEGATIVE_CONVERSATIONAL_QUERIES = [
    # 1. Exact query from problem statement
    "I am fine J.A.R.V.I.S., but what model are we using for you?",
    # 2-5. Standard self-referential / capability questions
    "What model are we using?",
    "How do you work?",
    "Who made you?",
    "Tell me about yourself.",
    # 6-10. Chitchat & persona greetings
    "What is this?",
    "How are you doing today?",
    "Hello Jarvis, I hope you are having a nice day.",
    "Thank you for the explanation.",
    "Are you online right now?",
    # 11-15. Questions containing spatial, aircraft, satellite, or maritime words
    "Can you explain how aircraft radar works?",
    "Do you like watching the satellites at night?",
    "What are the historic rules of maritime navigation?",
    "Explain how GPS satellites calculate position.",
    "Tell me a joke about airplanes.",
    # 16-20. General knowledge questions with prepositions (in, at, for, about)
    "Why is traffic always bad in big cities?",
    "What is the weather usually like in December?",
    "Can you explain quantum computing to me?",
    "What is the airspeed velocity of an unladen swallow?",
    "I was thinking about Paris yesterday.",
    # 21-25. Assistance & code questions
    "Can you help me debug this database query?",
    "Can you write a Python script for sorting algorithms?",
    "Where were you created?",
    "What languages can you speak?",
    "What is the capital of France?",
]


@pytest.mark.parametrize("query", NEGATIVE_CONVERSATIONAL_QUERIES)
def test_conversational_queries_produce_zero_nav_and_zero_layer_steps(query):
    """Verify that all conversational queries produce 0 navigation and 0 layer steps in analyze_goal."""
    loop = JarvisCognitiveLoop()
    steps = loop.analyze_goal(query)
    assert steps == [], f"Expected 0 steps for conversational query: '{query}', got: {steps}"

    # Also verify fallback explicitly produces 0 nav and 0 layer steps
    fallback_steps = loop._analyze_goal_fallback(query)
    nav_and_layers = [s for s in fallback_steps if s.get("action") in ("nav", "flights", "satellites", "vessels")]
    assert len(nav_and_layers) == 0, f"Fallback produced unwanted nav/layer steps for '{query}': {nav_and_layers}"


@pytest.mark.parametrize("query", NEGATIVE_CONVERSATIONAL_QUERIES)
def test_conversational_queries_do_not_glide_in_desktop_api(query):
    """Verify that JarvisAPI._resolve_and_glide_location returns False for conversational sentences."""
    api = JarvisAPI.__new__(JarvisAPI)
    glided = api._resolve_and_glide_location(query)
    assert glided is False, f"Expected _resolve_and_glide_location to return False for '{query}'"


def test_positive_explicit_navigation_commands_do_navigate():
    """Verify explicit navigation verbs correctly generate nav steps."""
    loop = JarvisCognitiveLoop()

    positive_commands = [
        ("fly to Paris", "paris"),
        ("go to Tokyo", "tokyo"),
        ("take me to London", "london"),
        ("navigate to Sydney", "sydney"),
        ("jump to Cairo", "cairo"),
        ("zoom to Berlin", "berlin"),
        ("look at Nilgiris", "nilgiris"),
        ("glide to Reykjavik", "reykjavik"),
        ("head to Dubai", "dubai"),
    ]

    for cmd, expected_loc in positive_commands:
        steps = loop._analyze_goal_fallback(cmd)
        nav_steps = [s for s in steps if s.get("action") == "nav"]
        assert len(nav_steps) == 1, f"Expected 1 nav step for '{cmd}', got: {steps}"
        assert expected_loc in nav_steps[0]["location"].lower()


def test_positive_explicit_tactical_layer_commands_do_toggle_layers():
    """Verify explicit layer commands with action verbs correctly generate layer steps."""
    loop = JarvisCognitiveLoop()

    layer_commands = [
        ("turn on radar", "flights"),
        ("show flights", "flights"),
        ("display airspace", "flights"),
        ("track satellites", "satellites"),
        ("show satellites", "satellites"),
        ("display vessels", "vessels"),
        ("scan maritime ships", "vessels"),
    ]

    for cmd, expected_action in layer_commands:
        steps = loop._analyze_goal_fallback(cmd)
        layer_steps = [s for s in steps if s.get("action") == expected_action]
        assert len(layer_steps) >= 1, f"Expected '{expected_action}' step for '{cmd}', got: {steps}"


def test_conversational_fast_path_bypasses_cognitive_loop_in_desktop():
    """Verify that in desktop.py, conversational queries bypass cognitive loop and classify_intent."""
    api = JarvisAPI.__new__(JarvisAPI)
    api._voice = MagicMock()
    api._emit = MagicMock()
    api._speak_and_suppress_echo = MagicMock()
    api._start_follow_up_window = MagicMock()
    api._run_ask = MagicMock()
    api.set_window_mode = MagicMock()
    api._resolve_and_plot_route = MagicMock(return_value=False)
    api._resolve_and_glide_location = MagicMock(return_value=False)
    api._context_manager = MagicMock()
    api._context_manager.classify_and_resolve.return_value = ("none", "none", {}, "")
    api._context_manager.get_active_pending_slot.return_value = None
    api._target = None
    api._cognitive_loop = MagicMock()

    test_q = "I am fine J.A.R.V.I.S., but what model are we using for you?"
    api._run_process_input(test_q)

    # Must route directly to _run_ask without calling cognitive_loop.analyze_goal or voice.classify_intent
    assert api._run_ask.called
    assert api._cognitive_loop.analyze_goal.call_count == 0
    assert api._voice.classify_intent.call_count == 0
