"""CLI acceptance tests (SRS section 7): the user-visible behaviors."""

import json

import pytest

from minibot.cli import main
from minibot.config import load_config
from minibot.state import BotStore


@pytest.fixture
def cli(tmp_path, monkeypatch) -> list[str]:
    """Isolated env + a config file; returns base args every test prepends."""
    for var in ("OLLAMA_HOST", "MINIBOT_OLLAMA_URL", "MINIBOT_MODEL", "MINIBOT_SANDBOX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MINIBOT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps({
            "sandbox_path": str(tmp_path / "sandbox"),
            "state_path": str(tmp_path / "state.json"),
            "log_path": str(tmp_path / "minibot.log"),
            "ping_timeout_s": 0.5,
            "request_timeout_s": 5.0,
        }),
        encoding="utf-8",
    )
    return ["--config", str(cfg)]


def _store(cli) -> BotStore:
    cfg = load_config(cli[1])
    return BotStore(cfg.state_path, cfg.decay)


def test_status_json(cli, capsys):
    assert main(cli + ["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) >= {"name", "mood", "hunger", "happiness", "energy", "health"}
    assert payload["name"] == "Pixel"


def test_feed_command_persists(cli, capsys):
    assert main(cli + ["feed"]) == 0
    assert "hunger" in capsys.readouterr().out
    assert _store(cli).state.hunger == pytest.approx(5, abs=0.01)


def test_rest_command(cli, capsys):
    assert main(cli + ["rest"]) == 0
    assert "energy" in capsys.readouterr().out


def test_play_when_tired_fails_gracefully(cli, capsys):
    store = _store(cli)
    store.state.energy = 5
    store.save()
    assert main(cli + ["play"]) == 1
    assert "too tired" in capsys.readouterr().err


def test_write_read_ls_roundtrip(cli, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _FakeStdin("print('hi')\n"))
    assert main(cli + ["write", "hello.py"]) == 0
    assert main(cli + ["ls"]) == 0
    out = capsys.readouterr().out
    assert "hello.py" in out
    assert main(cli + ["read", "hello.py"]) == 0
    assert "print('hi')" in capsys.readouterr().out


def test_traversal_rejected_with_safe_error(cli, capsys):
    rc = main(cli + ["read", "../../etc/passwd"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "outside the sandbox" in err
    assert "/data/data/" not in err and "root:" not in err  # no host internals leaked


def test_state_persists_across_invocations(cli):
    main(cli + ["feed"])
    main(cli + ["rest"])
    store = _store(cli)
    assert store.state.hunger == pytest.approx(8, abs=0.01)  # fed (5) then rest adds +3
    assert store.state.energy == pytest.approx(100, abs=0.01)


def test_doctor_reports_unreachable_ollama(cli, capsys, unused_port):
    rc = main(cli + ["--ollama-url", f"http://127.0.0.1:{unused_port}", "doctor"])
    assert rc == 1  # unreachable => failed check, reported immediately (fail fast)
    out = capsys.readouterr().out
    assert "ollama" in out
    assert "cannot reach Ollama" in out


def test_bad_config_exits_2(tmp_path, capsys):
    rc = main(["--config", str(tmp_path / "missing.json"), "status"])
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_unknown_config_key_warns(cli, capsys):
    from pathlib import Path

    cfg_path = Path(cli[1])
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["modle"] = "typo"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    assert main(cli + ["status"]) == 0
    assert "warning" in capsys.readouterr().err


def test_no_needle_flag_shows_disabled(cli, capsys, unused_port):
    rc = main(cli + ["--ollama-url", f"http://127.0.0.1:{unused_port}", "--no-needle", "doctor"])
    assert rc in (0, 1)  # doctor reflects ollama reachability; not what we assert here
    out = capsys.readouterr().out
    assert "needle" in out
    assert "disabled" in out


def test_no_such_file_error(cli, capsys):
    rc = main(cli + ["read", "ghost.txt"])
    assert rc == 1
    assert "no such file" in capsys.readouterr().err


class _FakeStdin:
    def __init__(self, data: str):
        self._data = data

    def read(self) -> str:
        return self._data

    def __iter__(self):
        return iter(self._data.splitlines(keepends=True))


def test_treat_command_spends_one(cli, capsys):
    assert main(cli + ["feed"]) == 0
    capsys.readouterr()
    assert main(cli + ["treat"]) == 0
    assert "treat" in capsys.readouterr().out.lower()
    assert _store(cli).state.treats == 0


def test_treat_without_inventory_errors(cli, capsys):
    rc = main(cli + ["treat"])
    assert rc == 1
    assert "no treats" in capsys.readouterr().err


def test_stats_json_records_a_sample(cli, capsys):
    assert main(cli + ["stats", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["samples"], "stats must never show an empty chart on a fresh bot"
    assert {"t", "hunger", "happiness", "energy", "health"} <= set(payload["samples"][-1])


def test_stats_chart_prints_series(cli, capsys):
    assert main(cli + ["stats"]) == 0
    out = capsys.readouterr().out
    assert "hunger" in out
    assert "treats" in out


def test_history_and_undo_roundtrip(cli, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", _FakeStdin("v1\n"))
    assert main(cli + ["write", "a.txt"]) == 0
    monkeypatch.setattr("sys.stdin", _FakeStdin("v2\n"))
    assert main(cli + ["write", "a.txt"]) == 0
    capsys.readouterr()

    assert main(cli + ["history"]) == 0
    assert "a.txt" in capsys.readouterr().out

    assert main(cli + ["undo", "a.txt"]) == 0
    assert "restored" in capsys.readouterr().out

    assert main(cli + ["read", "a.txt"]) == 0
    assert capsys.readouterr().out == "v1\n"

    # the overwritten entry was consumed; the original create entry remains
    assert main(cli + ["history"]) == 0
    out = capsys.readouterr().out
    assert "create" in out
    assert "(1 of 1 recorded writes)" in out


def test_undo_without_history_errors(cli, capsys):
    rc = main(cli + ["undo"])
    assert rc == 1
    assert "nothing to undo" in capsys.readouterr().err


def test_mcp_serve_over_piped_stdio(cli, capsys, monkeypatch):
    requests = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
    )
    monkeypatch.setattr("sys.stdin", _FakeStdin(requests))
    assert main(cli + ["mcp"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["result"]["serverInfo"]["name"] == "minibot"
    assert any(tool["name"] == "feed_bot" for tool in lines[1]["result"]["tools"])
