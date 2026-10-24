"""Write journal tests: /history and /undo semantics (FR-4.5)."""

import pytest

from minibot.sandbox import Sandbox, SandboxError


@pytest.fixture
def jsandbox(tmp_path):
    return Sandbox(tmp_path / "sbx", journal_path=tmp_path / "journal.jsonl", max_file_bytes=4096)


def test_create_is_journaled(jsandbox):
    jsandbox.write_file("a.txt", "one")
    entries = jsandbox.history()["entries"]
    assert len(entries) == 1
    assert entries[0]["path"] == "a.txt"
    assert entries[0]["action"] == "create"
    assert entries[0]["undoable"] is True


def test_overwrite_captures_previous_version(jsandbox):
    jsandbox.write_file("a.txt", "v1")
    jsandbox.write_file("a.txt", "v2")
    assert jsandbox.read_file("a.txt")["content"] == "v2"
    outcome = jsandbox.undo("a.txt")
    assert outcome["ok"] is True
    assert outcome["restored_bytes"] == 2
    assert jsandbox.read_file("a.txt")["content"] == "v1"


def test_undo_removes_created_file(jsandbox):
    jsandbox.write_file("new.txt", "hello")
    outcome = jsandbox.undo()
    assert outcome["ok"] is True
    assert outcome["deleted"] is True
    with pytest.raises(SandboxError, match="no such file"):
        jsandbox.read_file("new.txt")


def test_undo_defaults_to_most_recent_write(jsandbox):
    jsandbox.write_file("a.txt", "A")
    jsandbox.write_file("b.txt", "B")
    jsandbox.undo()
    assert jsandbox.read_file("a.txt")["content"] == "A"
    with pytest.raises(SandboxError, match="no such file"):
        jsandbox.read_file("b.txt")


def test_undo_consumes_the_entry(jsandbox):
    jsandbox.write_file("a.txt", "one")
    jsandbox.undo("a.txt")
    with pytest.raises(SandboxError, match="nothing to undo"):
        jsandbox.undo()


def test_history_lists_recent_writes_oldest_first(jsandbox):
    jsandbox.write_file("a.txt", "1")
    jsandbox.write_file("b.txt", "2")
    jsandbox.write_file("c.txt", "3")
    outcome = jsandbox.history(limit=2)
    assert [e["path"] for e in outcome["entries"]] == ["b.txt", "c.txt"]
    assert outcome["count"] == 2
    assert outcome["total"] == 3


def test_history_entries_do_not_leak_content(jsandbox):
    jsandbox.write_file("secret.txt", "hunter2")
    jsandbox.write_file("secret.txt", "changed")
    raw = jsandbox.history()["entries"][1]
    assert "hunter2" not in str(raw)
    assert "prev_b64" not in raw
    assert raw["prev_bytes"] == 7


def test_journal_survives_a_new_instance(jsandbox, tmp_path):
    jsandbox.write_file("a.txt", "one")
    again = Sandbox(tmp_path / "sbx", journal_path=tmp_path / "journal.jsonl")
    assert again.history()["count"] == 1
    assert again.undo("a.txt")["ok"] is True


def test_journal_is_trimmed(jsandbox):
    sandbox = Sandbox(jsandbox.root, journal_path=jsandbox.journal_path, max_journal_entries=3)
    for i in range(6):
        sandbox.write_file(f"f{i}.txt", "x")
    outcome = sandbox.history(limit=50)
    assert outcome["total"] == 3
    assert [e["path"] for e in outcome["entries"]] == ["f3.txt", "f4.txt", "f5.txt"]


def test_journal_disabled_by_default(sandbox, tmp_path):
    with pytest.raises(SandboxError, match="disabled"):
        sandbox.history()
    with pytest.raises(SandboxError, match="disabled"):
        sandbox.undo()


def test_writes_still_work_when_journal_unwritable(jsandbox, tmp_path):
    blocker = tmp_path / "journal.jsonl"
    blocker.write_text("", encoding="utf-8")  # a directory would fail harder; file is fine
    jsandbox.journal_path = tmp_path / "no" / "such" / "dir"
    outcome = jsandbox.write_file("ok.txt", "still works")
    assert outcome["ok"] is True
    assert jsandbox.read_file("ok.txt")["content"] == "still works"


def test_oversized_previous_version_marks_not_undoable(tmp_path):
    # a file written under a generous limit, then overwritten under a tight one:
    # the previous version is too big to snapshot, so it cannot be restored.
    generous = Sandbox(tmp_path / "sbx", journal_path=tmp_path / "j.jsonl", max_file_bytes=4096)
    generous.write_file("big.txt", "x" * 200)
    tight = Sandbox(tmp_path / "sbx", journal_path=tmp_path / "j.jsonl", max_file_bytes=64)
    result = tight.write_file("big.txt", "y")
    assert result["ok"] is True
    entries = tight.history()["entries"]
    assert entries[-1]["undoable"] is False
    with pytest.raises(SandboxError, match="cannot undo"):
        tight.undo("big.txt")


def test_tools_expose_history_and_undo(store, tmp_path):
    from minibot.tools import build_toolset

    sandbox = Sandbox(tmp_path / "sbx", journal_path=tmp_path / "j.jsonl")
    toolset = build_toolset(store, sandbox)
    toolset.execute("write_file", {"path": "a.txt", "content": "one"})
    toolset.execute("write_file", {"path": "a.txt", "content": "two"})
    history = toolset.execute("file_history", {"limit": 5})
    assert history["ok"] is True and history["count"] == 2
    undone = toolset.execute("undo_write", {"path": "a.txt"})
    assert undone["ok"] is True
    assert toolset.execute("read_file", {"path": "a.txt"})["content"] == "one"
