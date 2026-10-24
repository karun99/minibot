"""Config loading tests (FR-7): precedence, validation, fail-fast."""

import json

import pytest

from minibot.config import BotConfig, ConfigError, load_config


def _write(tmp_path, data, name="config.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("MINIBOT_MODEL", raising=False)
    cfg = load_config()
    assert cfg.model == "qwen2.5:7b"
    assert cfg.max_iterations == 5
    assert cfg.needle.enabled is False
    assert cfg.sandbox_path.endswith("sandbox")


def test_load_from_file(tmp_path):
    path = _write(tmp_path, {
        "model": "llama3.1:8b",
        "max_iterations": 7,
        "bot_name": "Botty",
        "needle": {"enabled": True, "mode": "heuristic", "min_confidence": 0.8},
        "decay": {"hunger_per_hour": 10.0},
    })
    cfg = load_config(str(path))
    assert cfg.model == "llama3.1:8b"
    assert cfg.max_iterations == 7
    assert cfg.bot_name == "Botty"
    assert cfg.needle.enabled is True
    assert cfg.needle.min_confidence == 0.8
    assert cfg.decay.hunger_per_hour == 10.0
    assert cfg.decay.energy_per_hour == 8.0  # untouched default
    assert cfg.source == str(path)


def test_missing_explicit_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(str(tmp_path / "nope.json"))


def test_invalid_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_config(str(path))


def test_unknown_key_warns(tmp_path):
    path = _write(tmp_path, {"modle": "typo"})
    cfg = load_config(str(path))
    assert any("modle" in w for w in cfg.warnings)


def test_bad_value_fails_fast(tmp_path):
    path = _write(tmp_path, {"max_iterations": 0})
    with pytest.raises(ConfigError, match="max_iterations"):
        load_config(str(path))


def test_bad_needle_mode(tmp_path):
    path = _write(tmp_path, {"needle": {"mode": "magic"}})
    with pytest.raises(ConfigError, match="needle.mode"):
        load_config(str(path))


def test_type_mismatch(tmp_path):
    path = _write(tmp_path, {"model": 42})
    with pytest.raises(ConfigError, match="must be a string"):
        load_config(str(path))


def test_cli_overrides_beat_file(tmp_path):
    path = _write(tmp_path, {"model": "from-file"})
    cfg = load_config(str(path), {"model": "from-cli", "needle.enabled": False})
    assert cfg.model == "from-cli"
    assert cfg.needle.enabled is False


def test_none_overrides_ignored(tmp_path):
    path = _write(tmp_path, {"model": "from-file"})
    cfg = load_config(str(path), {"model": None, "needle.enabled": None})
    assert cfg.model == "from-file"


def test_env_normalizes_url(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "192.168.1.5:11434")
    cfg = load_config()
    assert cfg.ollama_url == "http://192.168.1.5:11434"


def test_env_model_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIBOT_MODEL", "env-model")
    path = _write(tmp_path, {"model": "file-model"})
    cfg = load_config(str(path))
    assert cfg.model == "env-model"


def test_bad_url_rejected(tmp_path):
    path = _write(tmp_path, {"ollama_url": "ftp://nope"})
    with pytest.raises(ConfigError, match="ollama_url"):
        load_config(str(path))


def test_numeric_strings_coerced(tmp_path):
    path = _write(tmp_path, {"max_iterations": "9", "request_timeout_s": "30"})
    cfg = load_config(str(path))
    assert cfg.max_iterations == 9
    assert cfg.request_timeout_s == 30.0


def test_default_config_is_valid():
    cfg = BotConfig()
    from minibot.config import _finalize_paths, _validate

    _finalize_paths(cfg)
    _validate(cfg)  # must not raise
