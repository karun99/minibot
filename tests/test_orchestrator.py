"""Orchestrator tests (FR-2, NFR-2): tool loop, retries, limits, needle routing."""

import json

import pytest

from minibot.needle import HeuristicNeedle
from minibot.ollama_client import OllamaUnreachable
from minibot.orchestrator import Orchestrator
from tests.fakes import FakeClient, multi_call_resp, text_resp, tool_call_resp


@pytest.fixture
def orch(config, store, sandbox, toolset):
    def make(responses, needle=None, client=None):
        fake = client or FakeClient(responses)
        return Orchestrator(config, store, sandbox, toolset, fake, needle), fake

    return make


def test_simple_tool_then_answer(orch, store):
    o, fake = orch([tool_call_resp("feed_bot"), text_resp("Yum, all fed!")])
    turn = o.handle("feed my bot")
    assert turn.text == "Yum, all fed!"
    assert turn.tools_run == ["feed_bot"]
    assert turn.iterations == 2
    assert store.state.hunger == pytest.approx(5, abs=0.01)
    # second call saw the tool result
    tool_msgs = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs and json.loads(tool_msgs[0]["content"])["ok"] is True


def test_plain_chat_no_tools(orch):
    o, fake = orch([text_resp("Hey there! I'm Pixel.")])
    turn = o.handle("hello")
    assert turn.text == "Hey there! I'm Pixel."
    assert turn.tools_run == []
    assert fake.calls[0]["tools"], "tool schemas must be advertised"


def test_multi_tool_turn(orch, store):
    o, _ = orch([multi_call_resp([("feed_bot", {}), ("rest_bot", {})]), text_resp("done")])
    turn = o.handle("feed and then rest")
    assert sorted(turn.tools_run) == ["feed_bot", "rest_bot"]
    assert store.state.energy == pytest.approx(100)


def test_tool_error_fed_back_to_model(orch, store):
    store.state.energy = 5
    store.save()
    o, fake = orch([tool_call_resp("play_with_bot"), text_resp("Too tired, sorry!")])
    turn = o.handle("play")
    assert turn.text == "Too tired, sorry!"
    tool_msgs = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    payload = json.loads(tool_msgs[0]["content"])
    assert payload["ok"] is False
    assert "too tired" in payload["error"]
    assert turn.tools_run == []  # failed tools are not counted as run


def test_unknown_tool_reported_not_fatal(orch):
    o, fake = orch([tool_call_resp("teleport"), text_resp("Nope.")])
    turn = o.handle("teleport me")
    assert turn.text == "Nope."
    tool_msgs = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert "unknown tool" in json.loads(tool_msgs[0]["content"])["error"]


def test_max_iterations_graceful(config, orch):
    config.max_iterations = 3
    o, fake = orch([tool_call_resp("get_bot_status")] * 3)
    turn = o.handle("status please")
    assert "limit of 3 tool steps" in turn.text
    assert len(fake.calls) == 3
    assert turn.tools_run == ["get_bot_status"]


def test_malformed_string_arguments_get_one_retry(orch, store):
    o, fake = orch([
        tool_call_resp("feed_bot", "{not valid json"),
        text_resp("Fixed it."),
    ])
    turn = o.handle("feed")
    assert turn.text == "Fixed it."
    retry_msgs = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert "Malformed" in json.loads(retry_msgs[0]["content"])["error"]


def test_malformed_twice_graceful_error(orch):
    o, _ = orch([
        tool_call_resp("feed_bot", "{bad"),
        tool_call_resp("feed_bot", ["list"]),
    ])
    turn = o.handle("feed")
    assert "malformed" in turn.text.lower()


def test_missing_name_is_malformed(orch):
    o, _ = orch([tool_call_resp("", {}), text_resp("recovered")])
    turn = o.handle("feed")
    assert turn.text == "recovered"


def test_non_dict_arguments_malformed(orch):
    o, _ = orch([tool_call_resp("feed_bot", 42), text_resp("ok")])
    turn = o.handle("feed")
    assert turn.text == "ok"


def test_unreachable_raises(orch):
    o, _ = orch([OllamaUnreachable("cannot reach Ollama at http://x")])
    with pytest.raises(OllamaUnreachable):
        o.handle("hello")


def test_failed_turn_not_committed_to_history(orch):
    o, fake = orch([OllamaUnreachable("down")])
    with pytest.raises(OllamaUnreachable):
        o.handle("hello")
    assert o.history == []
    # a retry starts clean
    fake.responses = [text_resp("back online")]
    turn = o.handle("hello")
    assert turn.text == "back online"
    assert [m["role"] for m in o.history] == ["user", "assistant"]


def test_history_trimming(config, orch):
    config.history_user_turns = 3
    responses = []
    for i in range(6):
        responses.append(text_resp(f"reply {i}"))
    o, _ = orch(responses)
    for i in range(6):
        o.handle(f"message {i}")
    user_msgs = [m for m in o.history if m["role"] == "user"]
    assert len(user_msgs) == 3
    assert user_msgs[0]["content"] == "message 3"


def test_tool_results_clipped(config, sandbox, orch):
    config.max_tool_output_chars = 300
    o, fake = orch([
        tool_call_resp("write_file", {"path": "big.txt", "content": "x" * 5000}),
        tool_call_resp("read_file", {"path": "big.txt"}),
        text_resp("read it"),
    ])
    o.handle("write big file then read it")
    tool_msgs = [m for m in fake.calls[2]["messages"] if m["role"] == "tool"]
    # 300 chars of payload + a short truncation marker
    assert all(len(m["content"]) <= 340 for m in tool_msgs)


def test_empty_model_response_has_fallback(orch):
    o, _ = orch([text_resp("   ")])
    turn = o.handle("hello")
    assert "empty response" in turn.text


def test_text_json_tool_call_fallback(orch, store):
    """Small models emit tool JSON as fenced text instead of native tool_calls."""
    fenced = '```json\n{"name": "feed_bot", "arguments": {}}\n```'
    o, _ = orch([text_resp(fenced), text_resp("Fed!")])
    turn = o.handle("feed")
    assert turn.text == "Fed!"
    assert turn.tools_run == ["feed_bot"]
    assert store.state.hunger == pytest.approx(5, abs=0.01)


def test_text_tool_call_with_string_arguments(orch):
    blob = '{"name": "read_file", "arguments": "{\\"path\\": \\"x.py\\"}"}'
    o, _ = orch([text_resp(blob), text_resp("here it is")])
    toolset_write = o.toolset.execute("write_file", {"path": "x.py", "content": "hi"})
    assert toolset_write["ok"]
    turn = o.handle("read x.py")
    assert turn.tools_run == ["read_file"]


def test_text_json_with_unknown_name_stays_text(orch):
    weird = '{"name": "Feed the bot", "arguments": {"hunger": 100}}'
    o, fake = orch([text_resp(weird)])
    turn = o.handle("feed")
    assert turn.text == weird  # passed through untouched, no bogus execution
    assert turn.tools_run == []
    assert len(fake.calls) == 1


def test_text_fallback_used_at_most_once_per_turn(orch):
    o, fake = orch([text_resp('{"name": "feed_bot"}'), text_resp('{"name": "feed_bot"}')])
    turn = o.handle("feed")
    assert turn.tools_run == ["feed_bot"]
    assert len(fake.calls) == 2  # second JSON treated as the final answer


def test_empty_input_short_circuits(orch):
    o, fake = orch([])
    turn = o.handle("   ")
    assert turn.text == ""
    assert fake.calls == []


def test_needle_direct_route_skips_llm(orch, store):
    o, fake = orch([], needle=HeuristicNeedle())
    turn = o.handle("feed my pet")
    assert turn.route == "direct"
    assert turn.tools_run == ["feed_bot"]
    assert turn.needle_label == "pet_action"
    assert fake.calls == []  # LLM never consulted
    assert store.state.hunger == pytest.approx(5, abs=0.01)


def test_needle_vague_falls_to_loop(orch):
    o, fake = orch([text_resp("On it!")], needle=HeuristicNeedle())
    turn = o.handle("I was hungry once")
    assert turn.route == "loop"
    assert turn.needle_label == "pet_action"
    assert len(fake.calls) == 1


def test_needle_broken_never_breaks_turn(config, store, sandbox, toolset):
    class ExplodingNeedle:
        def classify(self, text):
            raise RuntimeError("needle exploded")

    fake = FakeClient([text_resp("still fine")])
    o = Orchestrator(config, store, sandbox, toolset, fake, ExplodingNeedle())
    turn = o.handle("hello")
    assert turn.text == "still fine"


def test_system_prompt_mentions_bot_and_sandbox(orch, sandbox):
    o, fake = orch([text_resp("hi")])
    o.handle("hello")
    system = fake.calls[0]["messages"][0]
    assert system["role"] == "system"
    assert "Pixel" in system["content"]
    assert sandbox.root.name in system["content"]
    assert "read_file" in system["content"]


def test_reset_clears_history(orch):
    o, _ = orch([text_resp("a"), text_resp("b")])
    o.handle("one")
    assert o.history
    o.reset()
    assert o.history == []
