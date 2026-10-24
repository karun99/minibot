"""Configuration loading.

Precedence (highest wins): CLI overrides > environment > config file > defaults.
The config file is JSON. Paths are expanded (~ and env vars) at load time.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, get_type_hints

__all__ = [
    "BotConfig",
    "ConfigError",
    "DecayRates",
    "NeedleConfig",
    "default_home",
    "load_config",
]


class ConfigError(Exception):
    """Invalid or unreadable configuration."""


def default_home() -> Path:
    """Base directory for state/sandbox/logs (MINIBOT_HOME or ~/.minibot)."""
    env = os.environ.get("MINIBOT_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".minibot"


@dataclass
class DecayRates:
    """Stat drift per hour of real (wall-clock) time."""

    hunger_per_hour: float = 18.0
    happiness_per_hour: float = 6.0
    energy_per_hour: float = 8.0
    health_damage_per_hour: float = 15.0
    health_regen_per_hour: float = 10.0


NEEDLE_MODES = ("heuristic", "ollama")


@dataclass
class NeedleConfig:
    """Optional intent pre-filter (FR-5). Disabled => everything goes to the Ollama loop."""

    enabled: bool = False
    mode: str = "heuristic"  # "heuristic" (zero-model) | "ollama" (tiny classifier model)
    model: str = "qwen2.5:0.5b"
    min_confidence: float = 0.9


@dataclass
class BotConfig:
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:7b"
    bot_name: str = "Pixel"
    sandbox_path: str = ""
    state_path: str = ""
    log_path: str = ""
    max_iterations: int = 5
    request_timeout_s: float = 120.0
    ping_timeout_s: float = 3.0
    max_file_bytes: int = 1_048_576
    max_tool_output_chars: int = 12_000
    history_user_turns: int = 8
    decay: DecayRates = field(default_factory=DecayRates)
    needle: NeedleConfig = field(default_factory=NeedleConfig)
    source: str = ""
    warnings: list[str] = field(default_factory=list, repr=False, compare=False)


# Backwards-compatible alias.
Config = BotConfig

_NESTED_TYPES = {"decay": DecayRates, "needle": NeedleConfig}
_RESERVED = ("source", "warnings")


def _coerce(key: str, value: Any, expected: Any, where: str) -> Any:
    def bad(detail: str) -> ConfigError:
        return ConfigError(f"{where}: '{key}' {detail}")

    if expected is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise bad("must be true or false")
    if expected is int:
        if isinstance(value, bool):
            raise bad("must be an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                pass
        raise bad("must be an integer")
    if expected is float:
        if isinstance(value, bool):
            raise bad("must be a number")
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                pass
        raise bad("must be a number")
    if expected is str:
        if isinstance(value, str):
            return value
        raise bad("must be a string")
    if expected in (list[str], "list[str]"):
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return value
        raise bad("must be a list of strings")
    raise bad(f"has unsupported type {expected!r}")


def _apply_mapping(obj: Any, raw: dict, where: str, warnings: list[str]) -> None:
    hints = get_type_hints(type(obj))
    for key, value in raw.items():
        if key not in hints or key in _RESERVED:
            warnings.append(f"{where}: unknown key '{key}' (ignored)")
            continue
        if key in _NESTED_TYPES:
            if not isinstance(value, dict):
                raise ConfigError(f"{where}: '{key}' must be a JSON object")
            _apply_mapping(getattr(obj, key), value, f"{where}.{key}", warnings)
            continue
        setattr(obj, key, _coerce(key, value, hints[key], where))


def _apply_overrides(cfg: BotConfig, overrides: dict | None) -> None:
    if not overrides:
        return
    hints = get_type_hints(BotConfig)
    for key, value in overrides.items():
        if value is None:
            continue
        if "." in key:
            head, _, tail = key.partition(".")
            nested = getattr(cfg, head, None)
            if head not in _NESTED_TYPES or not hasattr(nested, tail):
                raise ConfigError(f"unknown override '{key}'")
            setattr(nested, tail, value)
            continue
        if key not in hints or key in _RESERVED:
            raise ConfigError(f"unknown override '{key}'")
        setattr(cfg, key, _coerce(key, value, hints[key], "override"))


def _apply_env(cfg: BotConfig) -> None:
    host = os.environ.get("OLLAMA_HOST") or os.environ.get("MINIBOT_OLLAMA_URL")
    if host:
        cfg.ollama_url = _normalize_url(host)
    if os.environ.get("MINIBOT_MODEL"):
        cfg.model = os.environ["MINIBOT_MODEL"]
    if os.environ.get("MINIBOT_SANDBOX"):
        cfg.sandbox_path = os.environ["MINIBOT_SANDBOX"]


def _normalize_url(url: str) -> str:
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    return url.rstrip("/")


def _find_config_file(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path
    cwd_candidate = Path.cwd() / "config.json"
    if cwd_candidate.is_file():
        return cwd_candidate
    candidates = []
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(Path(xdg) / "minibot" / "config.json")
    candidates.append(Path.home() / ".config" / "minibot" / "config.json")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: invalid JSON ({e})") from e
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a JSON object")
    return raw


def _finalize_paths(cfg: BotConfig) -> None:
    home = default_home()
    if not cfg.sandbox_path:
        cfg.sandbox_path = str(home / "sandbox")
    if not cfg.state_path:
        cfg.state_path = str(home / "state.json")
    if not cfg.log_path:
        cfg.log_path = str(home / "minibot.log")
    for attr in ("sandbox_path", "state_path", "log_path"):
        value = os.path.expandvars(str(getattr(cfg, attr)))
        setattr(cfg, attr, str(Path(value).expanduser()))
    cfg.ollama_url = _normalize_url(cfg.ollama_url)


def _validate(cfg: BotConfig) -> None:
    def bad(msg: str) -> None:
        raise ConfigError(f"invalid config: {msg}")

    if not cfg.ollama_url.startswith(("http://", "https://")):
        bad("ollama_url must start with http:// or https://")
    if not cfg.model.strip():
        bad("model must not be empty")
    if not cfg.bot_name.strip():
        bad("bot_name must not be empty")
    if cfg.max_iterations < 1:
        bad("max_iterations must be >= 1")
    if cfg.request_timeout_s <= 0:
        bad("request_timeout_s must be > 0")
    if cfg.ping_timeout_s <= 0:
        bad("ping_timeout_s must be > 0")
    if cfg.max_file_bytes < 1:
        bad("max_file_bytes must be >= 1")
    if cfg.max_tool_output_chars < 200:
        bad("max_tool_output_chars must be >= 200")
    if cfg.history_user_turns < 1:
        bad("history_user_turns must be >= 1")
    if cfg.needle.mode not in NEEDLE_MODES:
        bad(f"needle.mode must be one of {NEEDLE_MODES}")
    if not 0.0 <= cfg.needle.min_confidence <= 1.0:
        bad("needle.min_confidence must be between 0 and 1")
    for name, value in vars(cfg.decay).items():
        if value < 0:
            bad(f"decay.{name} must be >= 0")


def load_config(explicit: str | None = None, overrides: dict | None = None) -> BotConfig:
    """Load configuration from file, environment and overrides (see module docstring)."""
    cfg = BotConfig()
    path = _find_config_file(explicit)
    if path is not None:
        _apply_mapping(cfg, _read_json(path), str(path), cfg.warnings)
        cfg.source = str(path)
    else:
        cfg.source = "built-in defaults"
    _apply_env(cfg)
    _apply_overrides(cfg, overrides)
    _finalize_paths(cfg)
    _validate(cfg)
    return cfg
