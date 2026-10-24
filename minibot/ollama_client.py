"""Minimal stdlib-only client for the Ollama HTTP API (no third-party deps)."""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("minibot.ollama")


class OllamaError(Exception):
    """Base class for Ollama communication problems."""


class OllamaUnreachable(OllamaError):
    """Ollama is not reachable (connection refused, DNS, timeout)."""


class OllamaHTTPError(OllamaError):
    """Ollama answered with an HTTP error status."""


@dataclass
class ChatResponse:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # [{"name": ..., "arguments": ...}]
    raw_message: dict = field(default_factory=dict)


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        ping_timeout: float = 3.0,
        temperature: float = 0.2,
        logger: logging.Logger | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.ping_timeout = ping_timeout
        self.temperature = temperature
        self.logger = logger or log

    # -- transport -----------------------------------------------------------

    def _request(self, path: str, payload: dict | None = None, timeout: float | None = None,
                 method: str = "POST") -> dict:
        url = self.base_url + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method=method
        )
        timeout = self.timeout if timeout is None else timeout
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise OllamaHTTPError(
                f"Ollama returned HTTP {e.code} for {path}" + (f": {detail}" if detail else "")
            ) from e
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise OllamaUnreachable(
                    f"Ollama at {self.base_url} timed out after {timeout}s"
                ) from e
            raise OllamaUnreachable(f"cannot reach Ollama at {self.base_url} ({reason})") from e
        except (TimeoutError, socket.timeout) as e:
            raise OllamaUnreachable(
                f"Ollama at {self.base_url} timed out after {timeout}s"
            ) from e
        except OSError as e:
            raise OllamaUnreachable(f"cannot reach Ollama at {self.base_url} ({e})") from e
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise OllamaError(f"invalid JSON from Ollama: {e}") from e
        if not isinstance(parsed, dict):
            raise OllamaError("unexpected response shape from Ollama")
        return parsed

    # -- API -----------------------------------------------------------------

    def ping(self) -> tuple[bool, str]:
        """Cheap liveness check against /api/tags (fails fast)."""
        try:
            self._request("/api/tags", payload=None, timeout=self.ping_timeout, method="GET")
        except OllamaError as e:
            return False, str(e)
        return True, "ok"

    def list_models(self) -> list[str]:
        data = self._request("/api/tags", payload=None, timeout=self.ping_timeout, method="GET")
        names = []
        for entry in data.get("models", []):
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                names.append(entry["name"])
        return names

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             model: str | None = None, temperature: float | None = None) -> ChatResponse:
        payload: dict = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self.temperature if temperature is None else temperature},
        }
        if tools:
            payload["tools"] = tools
        data = self._request("/api/chat", payload)
        message = data.get("message")
        message = message if isinstance(message, dict) else {}
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        calls: list[dict] = []
        raw_calls = message.get("tool_calls")
        if isinstance(raw_calls, list):
            for entry in raw_calls:
                if not isinstance(entry, dict):
                    continue
                fn = entry.get("function")
                if not isinstance(fn, dict):
                    continue
                calls.append({"name": fn.get("name") or "", "arguments": fn.get("arguments")})
        return ChatResponse(content=content, tool_calls=calls, raw_message=message)
