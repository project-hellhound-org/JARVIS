"""
Tests for Part 3: Wake-Word False Positives Reduction.
Verifies that:
1. 40+ ordinary sentences starting with rejected names and words (Gary, Jared, Jar, Yard, etc.)
   do NOT wake JARVIS.
2. Every accepted variant (jarvis, j.a.r.v.i.s, jarc, j.a.r.c, jarvik, jarbis, jervis, javis,
   plus edit distance 1 on 5+ letter words) DOES wake JARVIS.
3. Utterances where the wake word appears in the middle (without approved prefixes) do NOT wake.
"""
import pytest
from core.wake_word import WakeWordEngine


@pytest.fixture
def engine():
    return WakeWordEngine()


def test_forty_ordinary_sentences_do_not_wake(engine):
    """
    Assert that 40 ordinary sentences starting with rejected words and names
    (jar, jars, gary, jared, jarrod, jarrett, yard, yarn, garvis, car, park, part, etc.)
    do NOT wake JARVIS.
    """
    sentences = [
        # 1-4: jar / jars
        "Jar of peanut butter is in the pantry",
        "Jar of raspberry jam fell on the floor",
        "Jars are neatly arranged on the kitchen shelf",
        "Jars of spices were delivered yesterday",
        # 5-8: gary
        "Gary went to the grocery store this morning",
        "Gary called to discuss the quarterly report",
        "Gary works in the downtown office on weekdays",
        "Gary completed the software assignment on time",
        # 9-13: jared / jarrod / jarrett
        "Jared bought a brand new bicycle yesterday",
        "Jared sent over the revised presentation slides",
        "Jarrod is flying to Chicago next Monday",
        "Jarrett scored two goals in soccer practice",
        "Jarred olives are on sale at the supermarket",
        # 14-17: yard / yarn
        "Yard work needs to be finished before the weekend",
        "Yard sale begins at eight on Saturday morning",
        "Yarn was tangled into a knot on the rug",
        "Yarn for the winter sweater is woolen",
        # 18-19: garvis
        "Garvis arrived at the terminal an hour late",
        "Garvis forgot his notebook in the conference room",
        # 20-22: car / cars
        "Car needs an oil change and tire rotation",
        "Car keys were left on the hall table",
        "Cars were lined up waiting for the ferry",
        # 23-26: park / part
        "Park the vehicle in visitor space number four",
        "Park benches were freshly painted blue yesterday",
        "Part of the heating system needs replacement",
        "Parts arrived from the warehouse this morning",
        # 27-30: dart / dark / card / hard
        "Dart landed right in the center of the board",
        "Dark clouds are gathering on the western horizon",
        "Card payment was approved without issues",
        "Hard work always pays off in the end",
        # 31-36: Prefixed false trigger attempts (hey / ok / yo / wake up + rejected words)
        "Hey Gary, did you watch the match last night?",
        "Hey Jared, can you review this pull request?",
        "Hey Jarrod, are we meeting for coffee later?",
        "Ok Gary, let's wrap up this discussion",
        "Ok Jared, I will see you at the office",
        "Ok jar is sealed and stored away safely",
        # 37-41: yo / wake up prefixes with rejected words
        "Yo Gary, check out this interesting article",
        "Yo Jared, turn the television down a bit",
        "Yo yard maintenance is finally completed",
        "Wake up Gary, we need to leave for the airport",
        "Wake up Jared, breakfast is already served",
        "Wake up jar opener is inside the top drawer",
    ]

    assert len(sentences) >= 40, f"Expected at least 40 sentences, got {len(sentences)}"

    for sentence in sentences:
        matched, phrase = engine.check_stt_text_for_wake_or_aliases(sentence)
        assert matched is False, f"False wake triggered for: '{sentence}' (matched '{phrase}')"


def test_every_accepted_variant_wakes(engine):
    """
    Assert that every accepted variant of JARVIS wakes:
    Explicit list: jarvis, j.a.r.v.i.s, jarc, j.a.r.c, jarvik, jarbis, jervis, javis
    plus prefixes (hey, ok, yo, wake up) and 5+ letter edit distance 1 words.
    """
    # 1. Direct explicit spellings at utterance start
    direct_variants = [
        "jarvis, what is the status report?",
        "j.a.r.v.i.s, run system diagnostics",
        "jarc, check the radar screen",
        "j.a.r.c, what is the temperature outside?",
        "jarvik, report satellite telemetry",
        "jarbis, what is our flight altitude?",
        "jervis, open the tactical map",
        "javis, analyze incoming signals",
    ]
    for utterance in direct_variants:
        matched, phrase = engine.check_stt_text_for_wake_or_aliases(utterance)
        assert matched is True, f"Failed to match direct variant: '{utterance}'"
        assert phrase != ""

    # 2. Prefixes with accepted variants: hey, ok, yo, wake up
    prefixed_variants = [
        # hey
        "Hey Jarvis, give me the weather forecast",
        "Hey j.a.r.v.i.s, standing by for commands",
        "Hey jarc, zoom in on the target",
        "Hey j.a.r.c, report all aircraft",
        "Hey Jarvik, check communications",
        "Hey Jarbis, initiate tracking",
        "Hey Jervis, locate vessels",
        "Hey Javis, scan perimeter",
        # ok / okay
        "Ok Jarvis, display active layers",
        "Ok jarc, switch to satellite view",
        "Ok Jarvik, confirm flight corridor",
        "Ok Jarbis, silence alarm",
        "Okay Jervis, what is the distance?",
        "Ok Javis, lock radar target",
        # yo
        "Yo Jarvis, what is happening in the news?",
        "Yo jarc, bring up CCTV feeds",
        "Yo Jarvik, speed up data feed",
        "Yo Jarbis, verify the hash",
        "Yo Jervis, track the beacon",
        "Yo Javis, show coordinates",
        # wake up
        "Wake up Jarvis, operational check required",
        "Wake up jarc, tactical overview please",
        "Wake up Jarvik, full diagnostics",
        "Wake up Jarbis, commence telemetry",
        "Wake up Jervis, sound alarms",
        "Wake up Javis, power on sensors",
    ]
    for utterance in prefixed_variants:
        matched, phrase = engine.check_stt_text_for_wake_or_aliases(utterance)
        assert matched is True, f"Failed to match prefixed variant: '{utterance}'"
        assert phrase != ""

    # 3. Edit distance 1 on 5+ letter words (e.g. jarviz, jarviss, jarvi)
    edit_dist_variants = [
        "Jarviz, what is the altitude?",
        "Jarviss, verify target lock",
        "Jarvi, report current airspeed",
        "Hey Jarviz, check weather",
        "Ok Jarviss, proceed with flight plan",
    ]
    for utterance in edit_dist_variants:
        matched, phrase = engine.check_stt_text_for_wake_or_aliases(utterance)
        assert matched is True, f"Failed to match edit distance variant: '{utterance}'"
        assert phrase != ""


def test_wake_word_in_middle_of_utterance_does_not_wake(engine):
    """
    Assert that the wake word counts ONLY at the start of an utterance or right after
    hey/ok/yo/wake up. Middle or trailing mentions must NOT wake.
    """
    middle_mentions = [
        "I was talking about jarvis earlier today",
        "The project called jarvis is running smoothly",
        "Did you ask jarvis about the weather?",
        "We should configure jarvis before the demo starts",
        "He mentioned that jarvis was very responsive",
        "Tomorrow we will show jarvis to the team",
    ]
    for utterance in middle_mentions:
        matched, phrase = engine.check_stt_text_for_wake_or_aliases(utterance)
        assert matched is False, f"Unexpected wake for middle mention: '{utterance}'"
