"""Sandbox security tests (FR-4, NFR-3): traversal attempts must all fail."""

import os

import pytest

from minibot.sandbox import Sandbox, SandboxError


def test_write_read_roundtrip(sandbox):
    sandbox.write_file("hello.txt", "hi there")
    result = sandbox.read_file("hello.txt")
    assert result["content"] == "hi there"
    assert result["path"] == "hello.txt"


def test_write_creates_nested_dirs(sandbox):
    sandbox.write_file("a/b/c.txt", "deep")
    assert sandbox.read_file("a/b/c.txt")["content"] == "deep"


def test_parent_traversal_rejected(sandbox):
    with pytest.raises(SandboxError, match="outside the sandbox"):
        sandbox.read_file("../outside.txt")


def test_deep_traversal_rejected(sandbox):
    with pytest.raises(SandboxError):
        sandbox.read_file("foo/../../etc/passwd")


def test_absolute_path_outside_rejected(sandbox, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("secret", encoding="utf-8")
    with pytest.raises(SandboxError, match="outside the sandbox"):
        sandbox.read_file(str(victim))
    with pytest.raises(SandboxError):
        sandbox.read_file("/etc/passwd")


def test_absolute_path_inside_allowed(sandbox):
    target = str(sandbox.root / "inside.txt")
    sandbox.write_file(target, "ok")
    assert sandbox.read_file(target)["content"] == "ok"


def test_null_byte_rejected(sandbox):
    with pytest.raises(SandboxError, match="null byte"):
        sandbox.read_file("a\x00b")


def test_empty_path_rejected(sandbox):
    with pytest.raises(SandboxError, match="empty"):
        sandbox.read_file("   ")


def test_read_missing_file(sandbox):
    with pytest.raises(SandboxError, match="no such file"):
        sandbox.read_file("nope.txt")


def test_read_directory_rejected(sandbox):
    (sandbox.root / "adir").mkdir()
    with pytest.raises(SandboxError, match="directory"):
        sandbox.read_file("adir")


def test_binary_file_rejected(sandbox):
    (sandbox.root / "blob.bin").write_bytes(b"\x00\x01\xff\xfe")
    with pytest.raises(SandboxError, match="UTF-8"):
        sandbox.read_file("blob.bin")


def test_read_size_limit(sandbox):
    sandbox.max_file_bytes = 16
    (sandbox.root / "big.txt").write_text("x" * 64, encoding="utf-8")
    with pytest.raises(SandboxError, match="too large"):
        sandbox.read_file("big.txt")


def test_write_size_limit(sandbox):
    sandbox.max_file_bytes = 16
    with pytest.raises(SandboxError, match="too large"):
        sandbox.write_file("big.txt", "x" * 64)


def test_write_directory_target_rejected(sandbox):
    (sandbox.root / "adir").mkdir()
    with pytest.raises(SandboxError, match="directory"):
        sandbox.write_file("adir", "nope")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_symlink_escape_rejected(sandbox, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    try:
        (sandbox.root / "leak").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")
    with pytest.raises(SandboxError, match="outside the sandbox"):
        sandbox.read_file("leak")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_symlink_dir_escape_rejected(sandbox, tmp_path):
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    try:
        (sandbox.root / "linkdir").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")
    with pytest.raises(SandboxError):
        sandbox.list_dir("linkdir/..")
    with pytest.raises(SandboxError):
        sandbox.write_file("linkdir/../evil.txt", "nope")


def test_symlink_inside_allowed(sandbox):
    sandbox.write_file("real.txt", "data")
    try:
        (sandbox.root / "alias.txt").symlink_to(sandbox.root / "real.txt")
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")
    assert sandbox.read_file("alias.txt")["content"] == "data"


def test_list_dir(sandbox):
    sandbox.write_file("notes/todo.md", "# todo")
    (sandbox.root / "sub").mkdir()
    result = sandbox.list_dir(".")
    names = {entry["name"] for entry in result["entries"]}
    assert names == {"notes/", "sub/"}
    inside = sandbox.list_dir("notes")
    assert any(entry["name"] == "todo.md" for entry in inside["entries"])


def test_list_dir_truncation(sandbox):
    sandbox.max_list_entries = 3
    for i in range(6):
        sandbox.write_file(f"f{i}.txt", "x")
    result = sandbox.list_dir(".")
    assert result["count"] == 3
    assert "note" in result


def test_list_dir_on_file_rejected(sandbox):
    sandbox.write_file("a.txt", "x")
    with pytest.raises(SandboxError, match="not a directory"):
        sandbox.list_dir("a.txt")


def test_write_is_audited(sandbox, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="minibot.sandbox"):
        sandbox.logger = logging.getLogger("minibot.sandbox")
        sandbox.write_file("audit-me.txt", "12345")
    record = next(r for r in caplog.records if "file_write" in r.message)
    assert "path=audit-me.txt" in record.message
    assert "bytes=5" in record.message
