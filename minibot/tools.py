"""Tool registry (FR-3, NFR-6): JSON schemas for the model + handler functions.

Adding a tool = one register() call with a schema and a handler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from .sandbox import Sandbox, SandboxError
from .state import BotError, BotStore

log = logging.getLogger("minibot.tools")


class ToolError(Exception):
    """Bad arguments or an impossible tool action."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict], Any]

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _summarize(args: dict) -> dict:
    """Log-friendly args: long content becomes a length marker (NFR-5)."""
    out = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 120:
            out[key] = f"<{len(value)} chars>"
        else:
            out[key] = value
    return out


def _noargs() -> dict:
    return {"type": "object", "properties": {}, "required": []}


def _require_str(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"missing or invalid argument '{key}' (non-empty string required)")
    return value


def _require_content(args: dict) -> str:
    value = args.get("content")
    if value is None:
        raise ToolError("missing argument 'content' (string required)")
    if not isinstance(value, str):
        raise ToolError("invalid argument 'content' (string required)")
    return value


class Toolset:
    def __init__(self, logger: logging.Logger | None = None):
        self._tools: dict[str, Tool] = {}
        self.logger = logger or log

    def register(
        self, name: str, description: str, parameters: dict, handler: Callable[[dict], Any]
    ) -> Tool:
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        tool = Tool(name, description, parameters, handler)
        self._tools[name] = tool
        return tool

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, name: str, args: Any) -> dict:
        """Run a tool. Never raises: failures come back as {"ok": False, "error": ...}."""
        args = args if isinstance(args, dict) else {}
        self.logger.info("tool_call name=%s args=%s", name, _summarize(args))
        tool = self._tools.get(name)
        if tool is None:
            self.logger.warning("tool_call rejected: unknown tool %r", name)
            return {"ok": False, "error": f"unknown tool '{name}'", "available": self.names()}
        try:
            result = tool.handler(args)
        except (ToolError, BotError, SandboxError) as e:
            self.logger.info("tool_call refused name=%s error=%s", name, e)
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001 - a tool must never crash the loop
            self.logger.exception("tool crashed: %s", name)
            return {"ok": False, "error": f"internal error in {name}: {e}"}
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        result.setdefault("ok", True)
        self.logger.info("tool_call done name=%s ok=%s", name, result.get("ok"))
        return result


def build_toolset(store: BotStore, sandbox: Sandbox, logger: logging.Logger | None = None) -> Toolset:
    ts = Toolset(logger)
    name = store.state.name

    ts.register(
        "feed_bot",
        f"Feed the bot {name}. Reduces hunger (0 = full, 100 = starving), small happiness and "
        "health boost. Use when the user says the bot is hungry or asks to feed it.",
        _noargs(),
        lambda args: store.feed(),
    )
    ts.register(
        "play_with_bot",
        "Play with the bot. Raises happiness, costs energy, adds a little hunger. "
        "Fails if the bot is exhausted or sick.",
        _noargs(),
        lambda args: store.play(),
    )
    ts.register(
        "rest_bot",
        f"Let {name} rest/recharge. Restores energy, small hunger cost. Use when the bot is tired.",
        _noargs(),
        lambda args: store.rest(),
    )
    ts.register(
        "get_bot_status",
        "Return the bot's current stats (hunger/happiness/energy/health), mood and age in days.",
        _noargs(),
        lambda args: {"ok": True, "status": store.status()},
    )
    ts.register(
        "read_file",
        "Read a UTF-8 text file inside the sandbox directory. 'path' is relative to the sandbox "
        "root (e.g. 'notes/todo.md'). Absolute paths and '..' are rejected.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the sandbox root"}
            },
            "required": ["path"],
        },
        lambda args: sandbox.read_file(_require_str(args, "path")),
    )
    ts.register(
        "write_file",
        "Create or overwrite a UTF-8 text file inside the sandbox (e.g. write code). 'path' is "
        "relative to the sandbox root; absolute paths and '..' are rejected.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the sandbox root"},
                "content": {"type": "string", "description": "Full new contents of the file"},
            },
            "required": ["path", "content"],
        },
        lambda args: sandbox.write_file(_require_str(args, "path"), _require_content(args)),
    )
    ts.register(
        "list_dir",
        "List a directory inside the sandbox. 'path' defaults to the sandbox root; use it to "
        "browse files before reading or writing.",
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory relative to the sandbox root (default '.')",
                }
            },
            "required": [],
        },
        lambda args: sandbox.list_dir(args.get("path") or "."),
    )
    return ts
