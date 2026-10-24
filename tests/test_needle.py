"""Needle pre-filter tests (FR-5)."""

from minibot.needle import (
    Classification,
    HeuristicNeedle,
    OllamaNeedle,
    make_needle,
)
from tests.fakes import FakeClient, text_resp


def test_feed_my_pet_is_confident_direct():
    c = HeuristicNeedle().classify("feed my pet")
    assert c.label == "pet_action"
    assert c.tool == "feed_bot"
    assert c.confidence >= 0.9


def test_leading_verbs_route_directly():
    needle = HeuristicNeedle()
    assert needle.classify("feed").tool == "feed_bot"
    assert needle.classify("play with it").tool == "play_with_bot"
    assert needle.classify("go to sleep").tool == "rest_bot"
    assert needle.classify("status").tool == "get_bot_status"


def test_bot_is_tired():
    c = HeuristicNeedle().classify("the bot is tired")
    assert c.label == "pet_action"
    assert c.tool == "rest_bot"


def test_file_actions_detected():
    needle = HeuristicNeedle()
    assert needle.classify("read config.json").label == "file_action"
    assert needle.classify("list the files").label == "file_action"
    assert needle.classify("ls").label == "file_action"
    assert needle.classify("write a file called hello.py").label == "file_action"
    assert needle.classify("read ../../etc/passwd").label == "file_action"


def test_plain_chat_not_pet_action():
    needle = HeuristicNeedle()
    for text in ("hello there", "tell me a joke", "what is the capital of France"):
        c = needle.classify(text)
        assert c.label in ("chat", "unknown")


def test_negation_demotes_confidence():
    c = HeuristicNeedle().classify("don't feed the bot")
    assert c.confidence < 0.9
    assert c.tool is None or c.confidence < 0.9


def test_vague_mentions_stay_below_threshold():
    c = HeuristicNeedle().classify("I was hungry once")
    assert c.label == "pet_action"
    # A tool hint may be attached, but confidence must stay below the
    # direct-execution threshold so the LLM loop handles it correctly.
    assert c.confidence < 0.9


def test_mixed_intent_never_direct():
    c = HeuristicNeedle().classify("feed my pet and read config.json")
    assert c.tool is None
    assert c.confidence < 0.9


def test_multi_action_never_direct():
    c = HeuristicNeedle().classify("feed the bot and play with it")
    assert c.tool is None
    assert c.confidence < 0.9


def test_empty_input():
    c = HeuristicNeedle().classify("   ")
    assert c.label == "unknown"
    assert c.confidence == 0.0


def test_ollama_needle_parses_reply():
    client = FakeClient([text_resp('{"label":"file_action","confidence":0.99,"tool":""}')])
    c = OllamaNeedle(client, "tiny:1b").classify("read secrets.txt")
    assert c.label == "file_action"
    assert c.confidence == 0.99
    assert c.detail == "ollama"


def test_ollama_needle_strips_fences():
    client = FakeClient([text_resp('```json\n{"label":"chat","confidence":0.7,"tool":""}\n```')])
    c = OllamaNeedle(client, "tiny:1b").classify("hi")
    assert c.label == "chat"


def test_ollama_needle_falls_back_on_garbage():
    client = FakeClient([text_resp("i refuse to answer")])
    c = OllamaNeedle(client, "tiny:1b").classify("feed my pet")
    assert c.label == "pet_action"  # came from the heuristic fallback
    assert c.detail != "ollama"


def test_ollama_needle_falls_back_on_error():
    client = FakeClient([RuntimeError("boom")])
    c = OllamaNeedle(client, "tiny:1b").classify("feed")
    assert c.tool == "feed_bot"


def test_ollama_needle_rejects_bad_label():
    client = FakeClient([text_resp('{"label":"nonsense","confidence":0.9}')])
    c = OllamaNeedle(client, "tiny:1b").classify("feed")
    assert c.label == "pet_action"  # fallback


def test_make_needle_disabled(config):
    config.needle.enabled = False
    assert make_needle(config, FakeClient()) is None


def test_make_needle_heuristic(config):
    config.needle.enabled = True
    config.needle.mode = "heuristic"
    assert isinstance(make_needle(config, FakeClient()), HeuristicNeedle)


def test_make_needle_ollama(config):
    config.needle.enabled = True
    config.needle.mode = "ollama"
    needle = make_needle(config, FakeClient())
    assert isinstance(needle, OllamaNeedle)
    assert isinstance(needle.fallback, HeuristicNeedle)


def test_classification_dataclass_defaults():
    c = Classification("chat", 0.5)
    assert c.tool is None
    assert c.detail == ""
