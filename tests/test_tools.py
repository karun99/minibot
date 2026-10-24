"""Tool registry tests (FR-3, NFR-6)."""

import pytest


def test_schema_shape(toolset):
    schemas = toolset.schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {
        "feed_bot", "play_with_bot", "rest_bot", "get_bot_status",
        "read_file", "write_file", "list_dir",
    }
    for schema in schemas:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["description"].strip()
        assert fn["parameters"]["type"] == "object"


def test_required_args_declared(toolset):
    by_name = {s["function"]["name"]: s["function"]["parameters"] for s in toolset.schemas()}
    assert by_name["read_file"]["required"] == ["path"]
    assert set(by_name["write_file"]["required"]) == {"path", "content"}
    assert by_name["list_dir"]["required"] == []
    assert by_name["feed_bot"]["required"] == []


def test_feed_tool_changes_state(toolset, store):
    before = store.state.hunger
    outcome = toolset.execute("feed_bot", {})
    assert outcome["ok"] is True
    assert store.state.hunger < before
    assert outcome["status"]["name"] == "Pixel"


def test_status_tool(toolset):
    outcome = toolset.execute("get_bot_status", {})
    assert outcome["ok"] is True
    assert set(outcome["status"]) == {"name", "mood", "age_days", "hunger", "happiness", "energy", "health"}


def test_play_when_exhausted_returns_error(toolset, store):
    store.state.energy = 5
    store.save()
    outcome = toolset.execute("play_with_bot", {})
    assert outcome["ok"] is False
    assert "too tired" in outcome["error"]


def test_read_write_via_tools(toolset):
    assert toolset.execute("write_file", {"path": "x.py", "content": "print(1)\n"})["ok"]
    outcome = toolset.execute("read_file", {"path": "x.py"})
    assert outcome["content"] == "print(1)\n"


def test_read_missing_arg(toolset):
    outcome = toolset.execute("read_file", {})
    assert outcome["ok"] is False
    assert "'path'" in outcome["error"]


def test_write_missing_content(toolset):
    outcome = toolset.execute("write_file", {"path": "x.py"})
    assert outcome["ok"] is False
    assert "content" in outcome["error"]


def test_write_non_string_content(toolset):
    outcome = toolset.execute("write_file", {"path": "x.py", "content": {"a": 1}})
    assert outcome["ok"] is False


def test_traversal_via_tool_rejected(toolset):
    outcome = toolset.execute("read_file", {"path": "../../etc/passwd"})
    assert outcome["ok"] is False
    assert "outside the sandbox" in outcome["error"]


def test_unknown_tool(toolset):
    outcome = toolset.execute("launch_missiles", {})
    assert outcome["ok"] is False
    assert "unknown tool" in outcome["error"]
    assert "feed_bot" in outcome["available"]


def test_list_dir_default(toolset):
    toolset.execute("write_file", {"path": "a.txt", "content": "x"})
    outcome = toolset.execute("list_dir", {})
    assert outcome["ok"] is True
    assert any(entry["name"] == "a.txt" for entry in outcome["entries"])


def test_register_duplicate_rejected(toolset):
    with pytest.raises(ValueError, match="already registered"):
        toolset.register("feed_bot", "dup", {"type": "object", "properties": {}}, lambda a: {})


def test_adding_a_tool_is_trivial(toolset, store):
    """NFR-6: one schema entry + one handler."""
    toolset.register(
        "dance",
        "Make the bot dance.",
        {"type": "object", "properties": {}, "required": []},
        lambda args: {"ok": True, "message": "ta-da"},
    )
    outcome = toolset.execute("dance", {})
    assert outcome["message"] == "ta-da"
    assert "dance" in toolset.names()
