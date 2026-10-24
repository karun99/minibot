"""MCP server mode (`minibot mcp`): expose the bot's tools over stdio.

Speaks JSON-RPC 2.0 with newline-delimited messages, the MCP stdio transport,
so any MCP client (Claude Desktop, OpenCode, Cursor, ...) can drive the bot:
feed it, read/write files in the sandbox, and undo writes.

Stdout is reserved for protocol messages; diagnostics go to the log file.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TextIO

from . import __version__

log = logging.getLogger("minibot.mcp")

JSONRPC = "2.0"
PROTOCOL_VERSION = "2024-11-05"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": JSONRPC, "id": request_id, "error": {"code": code, "message": message}}


class MCPServer:
    """Adapts a minibot Toolset to the Model Context Protocol."""

    def __init__(self, toolset, name: str = "minibot", logger: logging.Logger | None = None):
        self.toolset = toolset
        self.name = name
        self.logger = logger or log
        self.initialized = False

    # -- protocol ------------------------------------------------------------

    def _initialize(self, params: dict) -> dict:
        self.initialized = True
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": self.name, "version": __version__},
        }

    def _tools_list(self, params: dict) -> dict:
        return {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.parameters or {"type": "object", "properties": {}},
                }
                for tool in self.toolset._tools.values()
            ]
        }

    def _tools_call(self, params: dict) -> dict:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise _ParamsError("missing tool 'name'")
        arguments: Any = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise _ParamsError("'arguments' must be an object")
        result = self.toolset.execute(name, arguments)
        text = json.dumps(result, indent=2, ensure_ascii=False)
        return {
            "content": [{"type": "text", "text": text}],
            "isError": not bool(result.get("ok", True)),
        }

    def handle(self, message: Any) -> dict | None:
        """Handle one decoded JSON-RPC message. None means 'no reply'."""
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "request must be a JSON object")
        request_id = message.get("id")
        method = message.get("method")
        if not isinstance(method, str):
            return _error(request_id, INVALID_REQUEST, "missing method")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(request_id, INVALID_PARAMS, "params must be an object")
        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "notifications/initialized":
                self.initialized = True
                return None
            elif method.startswith("notifications/"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = self._tools_list(params)
            elif method == "tools/call":
                result = self._tools_call(params)
            else:
                return _error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")
        except _ParamsError as e:
            return _error(request_id, INVALID_PARAMS, str(e))
        except Exception as e:  # noqa: BLE001 - a tool crash must not kill the server
            self.logger.exception("mcp method %s failed", method)
            return _error(request_id, INTERNAL_ERROR, f"{method} failed: {e}")
        if request_id is None:  # notification: no response
            return None
        return {"jsonrpc": JSONRPC, "id": request_id, "result": result}

    # -- stdio transport -----------------------------------------------------

    def serve(self, stdin: TextIO, stdout: TextIO) -> int:
        """Read newline-delimited requests until EOF. Returns a process exit code."""
        self.logger.info("mcp server listening on stdio")
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._send(stdout, _error(None, PARSE_ERROR, "invalid JSON"))
                continue
            response = self.handle(message)
            if response is not None:
                self._send(stdout, response)
        self.logger.info("mcp server: stdin closed, exiting")
        return 0

    @staticmethod
    def _send(stdout: TextIO, payload: dict) -> None:
        stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stdout.flush()


class _ParamsError(Exception):
    """Invalid MCP request parameters."""
