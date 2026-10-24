# Contributing to minibot

Thanks for helping out. minibot is a small, dependency-free project, so the
bar for a good contribution is: **tests pass, no new runtime dependencies**.

## Ground rules

- **Pure standard library.** Zero runtime dependencies is a feature (it is why
  the bot runs unchanged on Termux). A PR that adds a runtime dependency will
  be declined.
- **The sandbox is a safety boundary.** Anything that weakens path containment,
  size limits or the write audit needs a test proving the escape is still
  blocked.
- **Python 3.10+** on Linux, macOS, Windows and Termux. No platform-specific
  imports at module scope; use guarded, optional imports (see `tui._enable_windows_vt`).
- **Stdout belongs to the UI/protocol.** Diagnostics go to the log file, never
  stdout — that rule is what makes `minibot mcp` safe to pipe.

## Development setup

```bash
git clone https://github.com/karun99/minibot.git && cd minibot
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

Run the suite on a local disk. On a slow SD card, copy the tree to internal
storage first — the I/O, not the tests, is what hangs.

## Adding a tool

Tools are the whole point, and it takes one call in `minibot/tools.py`:

```python
ts.register(
    "my_tool",                      # name the model calls
    "One clear sentence of when to use it.",
    {"type": "object", "properties": {...}, "required": [...]},
    lambda args: do_the_thing(_require_str(args, "path")),
)
```

Handlers must return a dict (add `"ok": False, "error": ...` on refusal) and
must never raise: `Toolset.execute` already converts `ToolError`, `BotError`
and `SandboxError` into errors the model can read. Add a test in
`tests/test_tools.py` and, if the tool is user-facing, a slash command in
`tui.py`.

## Pull requests

1. One focused change per PR.
2. Tests for anything behavioral — including bug fixes (write the failing test
   first).
3. Keep the README honest: new commands and config keys get documented in the
   same PR.
4. Commit messages: `type: summary` (e.g. `feat:`, `fix:`, `docs:`, `test:`).

## Reporting bugs

Use the bug report template and include the output of `minibot doctor`, plus
`minibot --version`. Logs live at `~/.minibot/minibot.log` — paste the
relevant lines, not the whole file.
