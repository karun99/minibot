# minibot

A locally-hosted **bot for your terminal**, powered by [Ollama](https://ollama.com)
tool calling. Type natural language — the model picks tools, your bot eats,
plays, recharges, and can **read/write code files** in a restricted sandbox.

Zero third-party dependencies: pure Python stdlib. Runs on **Termux, Linux,
macOS and Windows**.

```
   .---. Pixel the bot · mood: happy · age 3d
  / o o \ Hunger    [██░░░░░░░░]  20/100
  \  w  / Happiness [███████░░░]  72/100
   '---' Energy     [██████░░░░]  65/100
          Health    [██████████] 100/100
❯ write a file hello.py that prints hello
» tools: write_file
Pixel: wrote hello.py (21 bytes), want me to run the idea past you?
```

## Requirements

- Python **3.10+**
- Ollama running locally (or on another machine on your LAN)

## Install Ollama

| Platform | How |
| --- | --- |
| Linux | `curl -fsSL https://ollama.com/install.sh \| sh` |
| macOS | `brew install ollama`, or download from ollama.com |
| Windows | `winget install Ollama.Ollama`, then start "Ollama" from the Start menu |
| Termux | `pkg install python` first. Ollama is **not** in the official Termux repo — options: the Termux User Repository (`pkg install tur-repo && pkg install ollama`, availability varies by architecture) or a community `aarch64-linux-android` build (see Ollama's GitHub issues). If your phone can't run Ollama, install it on your PC and point `ollama_url` at `http://<pc-ip>:11434` on the same Wi-Fi |

Pull a tool-calling-capable model (7B recommended on desktops):

```bash
ollama pull qwen2.5:7b        # or llama3.1:8b
# phones / weak CPUs:
ollama pull qwen2.5:3b
ollama pull qwen2.5:1.5b
```

Verify: `curl http://127.0.0.1:11434/api/tags` should list your models.

## Install minibot

```bash
git clone https://github.com/karun99/minibot.git && cd minibot
pip install .          # installs the `minibot` command
# — or run without installing:  python -m minibot
```

No `pip install` step needed beyond that — there are no runtime dependencies.

## Quick start

```bash
minibot doctor        # check python/config/sandbox/ollama/model
minibot run           # interactive terminal bot
```

Then chat, or use direct commands (they work even with Ollama stopped):

```
/feed  /play  /rest  /status  /ls  /read notes.md  /write notes.md
/needle on   /doctor   /clear   /help   /quit
```

Type `minibot run --offline` to start anyway when Ollama is down
(direct `/commands` only).

## One-shot commands (scriptable)

```bash
minibot status            # human readable
minibot status --json     # machine readable
minibot feed              # feed once
minibot ls src/
minibot read src/app.py
echo "print('hi')" | minibot write hello.py
minibot chat "read hello.py and tell me what it does"
```

## Configuration

Search order: `--config PATH` → `./config.json` →
`$XDG_CONFIG_HOME/minibot/config.json` → `~/.config/minibot/config.json` →
defaults. Precedence: **CLI flags > environment > config file > defaults**.

Copy `config.example.json` to `config.json` and edit. Every key is optional:

| Key | Default | Meaning |
| --- | --- | --- |
| `ollama_url` | `http://127.0.0.1:11434` | Ollama endpoint (also `OLLAMA_HOST`) |
| `model` | `qwen2.5:7b` | model for the main loop (also `MINIBOT_MODEL`) |
| `bot_name` | `Pixel` | your bot's name |
| `sandbox_path` | `~/.minibot/sandbox` | only directory file tools can touch (also `MINIBOT_SANDBOX`) |
| `state_path` | `~/.minibot/state.json` | persisted bot state |
| `log_path` | `~/.minibot/minibot.log` | rotating log (tool calls + file writes) |
| `max_iterations` | `5` | tool-loop cap per user message |
| `max_file_bytes` | `1048576` | read/write size limit (1 MB) |
| `needle.enabled` | `false` | intent pre-filter on/off |
| `needle.mode` | `heuristic` | `heuristic` (zero-model) or `ollama` (tiny classifier model) |
| `decay.*` | see example | stat drift per hour of real time |

`MINIBOT_HOME` (default `~/.minibot`) overrides where state/sandbox/logs live.

## The Needle pre-filter (optional)

When enabled, input is classified first into `bot_action / file_action / chat /
unknown`:

- **heuristic mode** — regex/keyword classifier, no model, instant, offline.
- **ollama mode** — a tiny classifier model (`needle.model`); any failure
  silently falls back to the heuristic, and any total failure falls back to the
  main loop (graceful degradation).
- Only *high-confidence, unambiguous* bot-care phrases ("feed my bot", "status")
  execute directly without the LLM; everything else — including vague or mixed
  requests — goes through the normal tool-calling loop.

## Sandbox safety

- All paths are resolved (symlinks followed) and must stay inside the sandbox.
- `..` traversal, absolute paths outside the root, escaping symlinks and null
  bytes are rejected with a safe error.
- Read/write capped at `max_file_bytes` (default 1 MB).
- Every write is logged with timestamp, path and size.

## Tests

```bash
pip install pytest
pytest
```

130+ tests: sandbox traversal attacks, state decay/persistence, tool schemas,
the ReAct loop (malformed-JSON retry, max iterations, history trimming),
Needle routing, the Ollama client against a local fake server, and CLI
acceptance tests.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Ollama unreachable` at startup | `ollama serve`, or check `ollama_url` / `OLLAMA_HOST` |
| `model '…' is not pulled` | `ollama pull <model>` |
| Bot replies but never uses tools | use a tool-capable model (`ollama show <model> \| grep Tools`) |
| Loop hits max iterations | ask for one thing at a time, or raise `max_iterations` |
| Colors/emoji look wrong | `MINIBOT_ASCII=1 minibot run`, or `NO_COLOR=1` |
| Windows console garbled | Windows Terminal recommended (VT sequences auto-enabled) |

## Layout

```
minibot/
  config.py          config file + env + CLI precedence
  state.py           bot stats, time decay, atomic JSON persistence
  sandbox.py         path containment, size limits, write audit
  tools.py           tool schemas + handlers (add a tool in ~10 lines)
  ollama_client.py   stdlib HTTP client for /api/chat and /api/tags
  needle.py          optional intent pre-filter (heuristic | ollama)
  orchestrator.py    ReAct loop, malformed-retry, history trimming
  tui.py             terminal UI: sprite, bars, slash commands
  cli.py             run/status/feed/play/rest/ls/read/write/chat/doctor
  checks.py          health checks shared by doctor and /doctor
docs/SRS.md          the requirements this implements
tests/               pytest suite
```

## License

MIT — see [LICENSE](LICENSE).
