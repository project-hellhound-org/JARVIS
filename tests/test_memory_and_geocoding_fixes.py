import pytest
from core.jarvis_memory import JarvisMemory
from core.agent_router import AgentRouter
from core.system_skills import SystemSkillEngine
from frontend.desktop import resolve_geospatial_coordinates, JarvisAPI


def test_memory_get_recent_speech_patterns_and_store_custom_rule(tmp_path):
    mem_file = tmp_path / "test_mem.json"
    mem = JarvisMemory(filepath=str(mem_file))

    # Verify get_recent_speech_patterns exists and returns list without AttributeError
    patterns = mem.get_recent_speech_patterns(limit=5)
    assert isinstance(patterns, list)

    # Store custom rule
    rule = "Daddy is Om means that I am as a Daddy and Om is with you."
    success = mem.store_custom_rule(rule)
    assert success is True

    # Check that rule is in speech patterns and summary prompt
    patterns_after = mem.get_recent_speech_patterns(limit=5)
    assert any("Daddy is Om" in p for p in patterns_after)

    summary = mem.get_memory_summary_for_prompt()
    assert "Daddy is Om" in summary
    assert "Operator Custom Rules & Phrase Meanings:" in summary


def test_agent_router_memory_store_vs_recall():
    router = AgentRouter()

    # Rule definition / memory store instruction
    t1 = "J.A.R.V.I.S., Daddy is Om doesn't mean Daddy is coming home. It says that I am as a Daddy and Om is with you."
    handled1, ack1, task1, cat1 = router.route_input(t1)
    assert handled1 is True
    assert cat1 == "memory_store"
    assert task1.type == "memory_store"
    assert "Daddy is Om" in task1.data.get("rule", "")

    # Another store phrasing
    t2 = "remember this log for me so next time you don't run some diagnosis"
    handled2, ack2, task2, cat2 = router.route_input(t2)
    assert handled2 is True
    assert cat2 == "memory_store"
    assert task2.type == "memory_store"

    # Memory recall query
    t3 = "what did we discuss yesterday?"
    handled3, ack3, task3, cat3 = router.route_input(t3)
    assert handled3 is True
    assert cat3 == "memory_recall"
    assert task3.type == "memory_recall"


def test_system_skills_memory_store_execution():
    skills = SystemSkillEngine()
    # Test try_execute with memory store phrase
    handled, msg, is_search, query, payload = skills.try_execute(
        "remember that Daddy is Om means Daddy is with you"
    )
    assert handled is True
    assert is_search is False
    assert payload.get("action_type") == "MEMORY_STORE"
    assert "committed that rule to persistent memory" in msg.lower()


def test_context_aware_geocoding():
    # NYC context bias for Jamaica
    res_jamaica = resolve_geospatial_coordinates("Jamaica", bias_lat=40.71, bias_lon=-74.00, context_name="New York")
    assert res_jamaica is not None
    assert abs(res_jamaica[0] - 40.7027) < 0.1
    assert abs(res_jamaica[1] - (-73.7890)) < 0.1
    assert "Jamaica" in res_jamaica[2]

    # Brooklyn default to NY
    res_brooklyn = resolve_geospatial_coordinates("Brooklyn")
    assert res_brooklyn is not None
    assert abs(res_brooklyn[0] - 40.6782) < 0.1
    assert abs(res_brooklyn[1] - (-73.9442)) < 0.1
    assert "Brooklyn" in res_brooklyn[2]

    # South Richmond Hill
    res_srh = resolve_geospatial_coordinates("South Richmond Hill")
    assert res_srh is not None
    assert abs(res_srh[0] - 40.6937) < 0.1

    # South Austin Park
    res_sap = resolve_geospatial_coordinates("South Austin Park")
    assert res_sap is not None
    assert abs(res_sap[0] - 40.7161) < 0.1




def test_multi_word_arc_annotation():
    api = JarvisAPI(initial_mode="full")
    emitted = []
    api._emit = lambda event, data: emitted.append((event, data))

    # Test multi-word quoted locations
    raw_cmd = 'arc from="South Richmond Hill" to="South Austin Park" label="LOCAL TRANSIT"'
    api._execute_tactical_annotate(raw_cmd)

    assert len(emitted) == 1
    event, data = emitted[0]
    assert event == "annotate_map"
    assert data["action"] == "arc"
    assert abs(data["from_lat"] - 40.6937) < 0.1
    assert abs(data["to_lat"] - 40.7161) < 0.1
    assert data["label"] == "LOCAL TRANSIT"
    assert "South Richmond Hill" in data["from_label"]
    assert "South Austin Park" in data["to_label"]


def test_resolve_and_plot_route_fast_path():
    api = JarvisAPI(initial_mode="full")
    emitted = []
    api._emit = lambda event, data: emitted.append((event, data))
    api._speak_and_suppress_echo = lambda text: None

    handled = api._resolve_and_plot_route("can you show a route from Brooklyn to South Richmond Hill?")
    assert handled is True
    assert any(e[0] == "annotate_map" and e[1].get("action") == "arc" for e in emitted)
