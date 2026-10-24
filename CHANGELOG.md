# Changelog

All notable changes to minibot are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are
semantic.

## [1.1.0] - 2026-10-24

### Added

- **MCP server mode** (`minibot mcp`): serves the bot's tools over stdio using
  the Model Context Protocol, so Claude Desktop, Cursor, OpenCode and other MCP
  clients can feed the bot, read/write sandbox files and undo writes. Stdout is
  protocol-only; diagnostics go to the log file.
- **Treats inventory**: caring for the bot (feed/rest earn 1, play earns 2)
  earns treats, capped at 20; `give_treat` / `/treat` spends one for a small
  happiness, health and energy boost. Count shows in `/status` and `minibot status`.
- **Stat history** (`/stats`, `minibot stats [--json] [--points N]`): the store
  keeps a bounded sample series (240 samples) and the CLI/TUI render it as a
  chart with a trend line.
- **Write journal — `/history` and `/undo`** (`minibot history`,
  `minibot undo [path]`): every sandbox write snapshots the previous contents
  (bounded by `max_file_bytes`) into `journal.jsonl`; undo restores it, or
  deletes a file the write created. Also exposed to the model as
  `file_history` and `undo_write` tools.
- Config keys `journal_path` and `journal_entries`.
- CI (GitHub Actions): pytest on Python 3.10–3.14 plus macOS/Windows, and a CLI
  smoke job that exercises the MCP handshake without Ollama.

### Changed

- Stat decay is now **mood-modulated**: neglect (starving, sick, exhausted)
  compounds the happiness drain, and a thriving bot (well fed, rested, healthy)
  slowly cheers up. Baseline per-hour rates are unchanged at the extremes the
  old model already handled.

### Fixed

- Unreachable-Ollama messages now always start with `cannot reach Ollama`,
  including timeouts — on Windows a closed port surfaces as a timeout rather
  than a refusal, which previously produced a different (and confusing) line in
  `minibot doctor`.
- Stats chart glyphs fall back to ASCII when stdout is not UTF-8 (`MINIBOT_ASCII`
  or a non-UTF-8 console such as a Windows code page).
- Tests assert the containment *invariant* for symlink escapes on Windows,
  where `linkdir/..` resolves lexically instead of following the symlink.

## [1.0.0] - 2026-10-24

Initial release: Ollama tool-calling loop, pet state with time-based decay,
sandboxed file read/write with traversal protection and audit logging, optional
Needle intent pre-filter (heuristic or tiny-model), terminal UI, scriptable
one-shot commands, `doctor` health checks, 133 tests. Pure standard library;
runs on Termux, Linux, macOS and Windows.
