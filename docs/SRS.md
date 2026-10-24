# Software Requirements Specification

# Mini Bot — Ollama-Based Terminal Bot Agent

Version: 1.0
Status: Draft

> v1.0 note: the product is realized as a **terminal UI** (Termux / Linux / macOS /
> Windows) rather than a web UI, and the virtual creature is referred to as a
> **bot** throughout.

---

## 1. Introduction

### 1.1 Purpose

This document specifies the requirements for a locally-hosted virtual bot that
accepts natural-language commands, maintains bot state, and can read/write code
files through an LLM-driven tool-calling loop powered by Ollama.

### 1.2 Scope

- In scope: local Ollama inference, tool calling, bot state management, sandboxed
  file read/write, minimal terminal UI.
- Out of scope: cloud deployment (Vercel), multi-user accounts, real-money
  transactions, mobile native apps.

### 1.3 Definitions

| Term | Meaning |
| --- | --- |
| Ollama | Local LLM runtime exposing an HTTP API |
| Tool calling | Model emits structured JSON selecting a function + arguments |
| Needle | Optional ultra-small (8–29 MB) intent classifier |
| Bot state | Hunger, happiness, energy, health, name, age |
| Sandbox | Restricted directory the agent may read/write |

---

## 2. Overall Description

### 2.1 Product Perspective

A single-user, locally-run application. Three layers:

1. **Terminal UI** — bot status panel + chat input (stdin/stdout)
2. **Orchestrator** — ReAct loop: model → tool call → execute → feed back
3. **Runtime** — Ollama (LLM) + filesystem sandbox

### 2.2 User Characteristics

Single user, technically comfortable running Ollama locally. No authentication
required.

### 2.3 Assumptions & Constraints

- Ollama is installed and running on localhost:11434.
- A tool-calling-capable model is pulled (e.g., Qwen2.5-7B-Instruct or
  Llama 3.1-8B).
- Filesystem access is restricted to a configured sandbox directory.
- Needle is optional; the system must function without it.
- No third-party Python dependencies (stdlib only) so it runs unchanged on
  Termux, Linux, macOS and Windows with Python 3.10+.

---

## 3. Functional Requirements

### FR-1 Bot State Management

- FR-1.1 The system shall persist bot state (name, hunger, happiness, energy,
  health, age) across sessions.
- FR-1.2 State shall decay over time (e.g., hunger increases every N minutes).
- FR-1.3 State shall be stored in a local JSON file or SQLite DB.

### FR-2 Natural-Language Command Handling

- FR-2.1 The system shall accept free-text user input.
- FR-2.2 The system shall send input + tool schema + conversation history to
  Ollama.
- FR-2.3 The system shall execute any tool call returned by the model and feed
  the result back.
- FR-2.4 The loop shall terminate when the model returns a final text response
  or a max-iteration limit (default 5) is reached.

### FR-3 Tool Set (Minimum)

| Tool | Purpose | Parameters |
| --- | --- | --- |
| feed_bot | Reduce hunger | none |
| play_with_bot | Increase happiness, reduce energy | none |
| rest_bot | Restore energy | none |
| get_bot_status | Return current state | none |
| read_file | Read file in sandbox | path: string |
| write_file | Write file in sandbox | path: string, content: string |
| list_dir | List sandbox directory | path: string |

### FR-4 File Operations Safety

- FR-4.1 All file paths shall be resolved and validated to remain inside the
  sandbox root.
- FR-4.2 Path traversal (`..`, absolute paths outside sandbox, symlinks escaping
  sandbox) shall be rejected.
- FR-4.3 File size limit for read/write shall be configurable (default 1 MB).
- FR-4.4 Write operations shall be logged (timestamp, path, size).

### FR-5 Optional Needle Pre-filter

- FR-5.1 If enabled, user input shall first be classified by Needle into
  {pet_action, file_action, chat, unknown}. *(Renamed: `bot_action`.)*
- FR-5.2 Classification shall route input to the appropriate handler; unknown
  falls through to the main Ollama loop. High-confidence `bot_action` runs the
  matching tool directly; everything else goes through the LLM loop.
- FR-5.3 If Needle is unavailable or disabled, all input goes directly to the
  Ollama loop (graceful degradation).

### FR-6 Terminal Interface

- FR-6.1 The UI shall display bot state (visual sprite + numeric bars).
- FR-6.2 The UI shall provide a chat input and display conversation history in
  the terminal scrollback; slash commands (`/feed`, `/read`, …) provide direct,
  offline-capable actions.
- FR-6.3 The UI shall reflect state changes within 1 second of a tool
  execution (status panel re-renders immediately after every action).

### FR-7 Configuration

- FR-7.1 Configurable via a single config file: Ollama URL, model name, sandbox
  path, max iterations, Needle on/off. Precedence: CLI > environment >
  config file > defaults.
- FR-7.2 The system shall fail fast with a clear error if Ollama is
  unreachable (startup ping, and per-turn error messages instead of hanging).
  `minibot run --offline` starts deliberately degraded.

---

## 4. Non-Functional Requirements

| ID | Requirement |
| --- | --- |
| NFR-1 | Performance: tool-call round trip ≤ 5s on a mid-range CPU with a 7B Q4 model |
| NFR-2 | Reliability: if the model emits malformed tool JSON, the system retries once, then returns a graceful error |
| NFR-3 | Security: sandbox enforcement (FR-4) must be tested with traversal attempts |
| NFR-4 | Portability: runs on Linux/macOS/Windows/Termux with Python 3.10+ and Ollama installed |
| NFR-5 | Observability: all tool calls and file writes logged to a local log file |
| NFR-6 | Extensibility: adding a new tool requires only a schema entry + handler function |
| NFR-7 | Offline: no network calls except to local Ollama |

---

## 5. System Architecture

```
[Terminal UI: status panel + chat (stdin/stdout)]
          │ function calls
          ▼
[Orchestrator: ReAct loop]
   ├── (optional) Needle pre-filter
   ├── Ollama /api/chat (tool calling)
   ├── Tool executor (bot state + sandboxed FS)
   └── Logger
          │
          ▼
[Ollama runtime] + [sandbox dir] + [state JSON]
```

---

## 6. External Interfaces

- **Ollama API:** POST `/api/chat` with `tools` array; expects
  `message.tool_calls` in response. Also GET `/api/tags` for the liveness
  check.
- **Filesystem:** sandbox directory only.
- **Terminal UI ↔ Orchestrator:** in-process calls; CLI subcommands
  (`status`, `feed`, `read`, `write`, `ls`, `chat`, `doctor`) for scripted use.

---

## 7. Acceptance Criteria (MVP)

1. User types "feed my bot" → hunger decreases, UI updates.
2. User types "read config.json" → file content returned if inside sandbox.
3. User types "read ../../etc/passwd" → rejected with a safe error.
4. Restart app → bot state persists.
5. Disable Needle → all commands still work via Ollama.
6. Ollama stopped → app shows "Ollama unreachable" instead of hanging.

---

## 8. Future Considerations (Out of Scope for v1)

- Multi-bot support
- Voice input
- Web search tool (via MCP)
- Model hot-swapping
