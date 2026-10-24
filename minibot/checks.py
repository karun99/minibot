"""Environment health checks shared by `minibot doctor` and the TUI (/doctor)."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from . import __version__
from .ollama_client import OllamaError


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    required: bool = True


def collect_checks(config, client, sandbox, store) -> list[CheckResult]:
    checks: list[CheckResult] = [
        CheckResult("python", sys.version_info >= (3, 10), sys.version.split()[0]),
        CheckResult("minibot", True, __version__),
        CheckResult("config", True, config.source),
    ]

    try:
        store.path.parent.mkdir(parents=True, exist_ok=True)
        probe = store.path.parent / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(CheckResult("state", True, str(store.path)))
    except OSError as e:
        checks.append(CheckResult("state", False, f"{store.path}: {e}"))

    try:
        probe = sandbox.root / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(CheckResult("sandbox", True, str(sandbox.root)))
    except OSError as e:
        checks.append(CheckResult("sandbox", False, f"{sandbox.root}: {e}"))

    reachable, detail = client.ping()
    checks.append(CheckResult("ollama", reachable, f"{client.base_url} — {detail}"))
    if reachable:
        try:
            models = client.list_models()
        except OllamaError as e:
            checks.append(CheckResult("model", False, f"could not list models: {e}"))
        else:
            present = any(
                m == config.model or m.startswith(config.model + ":") for m in models
            )
            detail = (
                "installed"
                if present
                else f"not pulled yet — run: ollama pull {config.model}"
            )
            checks.append(CheckResult("model", present, detail))
    else:
        checks.append(CheckResult("model", False, "skipped (ollama unreachable)", required=False))

    nc = config.needle
    if nc.enabled:
        needle_detail = f"enabled (mode={nc.mode}"
        if nc.mode == "ollama":
            needle_detail += f", model={nc.model}"
        needle_detail += ")"
    else:
        needle_detail = "disabled — all input goes to the Ollama loop"
    checks.append(CheckResult("needle", True, needle_detail, required=False))
    return checks


def print_checks(checks: list[CheckResult], emit=print, unicode_ok: bool = True) -> bool:
    """Print a checklist; returns True when all required checks passed."""
    marks = ("ok", "FAIL") if not unicode_ok else None
    for check in checks:
        if check.ok:
            mark = "ok" if not unicode_ok else "✔"
        elif check.required:
            mark = "x" if not unicode_ok else "✖"
        else:
            mark = "!" if not unicode_ok else "!"
        line = f"  [{mark}] {check.name:<8} {check.detail}" if not unicode_ok else \
               f"  {mark} {check.name:<8} {check.detail}"
        emit(line)
    return all(check.ok for check in checks if check.required)
