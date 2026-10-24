"""Ollama client tests against a local fake HTTP server (no external network)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from minibot.ollama_client import (
    OllamaClient,
    OllamaError,
    OllamaHTTPError,
    OllamaUnreachable,
)

_STATE = {"reply": {}, "status": 200}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _respond(self):
        body = json.dumps(_STATE["reply"]).encode()
        self.send_response(_STATE["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _STATE["reply"] = {"models": [{"name": "test:1b"}]}
        self._respond()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        _STATE["last_request"] = json.loads(self.rfile.read(length) or b"{}")
        self._respond()


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    old = dict(_STATE)
    _STATE.update({"reply": {}, "status": 200, "last_request": {}})
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    _STATE.clear()
    _STATE.update(old)
    httpd.shutdown()
    httpd.server_close()


def test_chat_parses_text(server):
    _STATE["reply"] = {"message": {"role": "assistant", "content": "hello!"}}
    client = OllamaClient(server, "test:1b")
    resp = client.chat([{"role": "user", "content": "hi"}])
    assert resp.content == "hello!"
    assert resp.tool_calls == []
    sent = _STATE["last_request"]
    assert sent["model"] == "test:1b"
    assert sent["stream"] is False


def test_chat_parses_tool_calls(server):
    _STATE["reply"] = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "feed_bot", "arguments": {"a": 1}}},
                {"function": {"name": "read_file", "arguments": "{\"path\": \"x.py\"}"}},
            ],
        }
    }
    client = OllamaClient(server, "test:1b")
    resp = client.chat([{"role": "user", "content": "go"}], tools=[{"type": "function"}])
    assert [c["name"] for c in resp.tool_calls] == ["feed_bot", "read_file"]
    assert resp.tool_calls[1]["arguments"] == '{"path": "x.py"}'  # string form preserved
    assert _STATE["last_request"]["tools"] == [{"type": "function"}]


def test_chat_model_override(server):
    _STATE["reply"] = {"message": {"content": "x"}}
    client = OllamaClient(server, "main:1b")
    client.chat([], model="tiny:0.5b", temperature=0.0)
    assert _STATE["last_request"]["model"] == "tiny:0.5b"
    assert _STATE["last_request"]["options"]["temperature"] == 0.0


def test_http_error_raises(server):
    _STATE["status"] = 500
    _STATE["reply"] = {"error": "model not found"}
    client = OllamaClient(server, "test:1b")
    with pytest.raises(OllamaHTTPError, match="500"):
        client.chat([])


def test_non_object_response_raises(server):
    _STATE["reply"] = ["not", "an", "object"]
    client = OllamaClient(server, "test:1b")
    with pytest.raises(OllamaError, match="unexpected response shape"):
        client.chat([])


def test_connection_refused_raises(unused_port):
    client = OllamaClient(f"http://127.0.0.1:{unused_port}", "test:1b", ping_timeout=0.5)
    with pytest.raises(OllamaUnreachable, match="cannot reach Ollama"):
        client.chat([])


def test_ping_success_and_failure(server, unused_port):
    ok, detail = OllamaClient(server, "test:1b", ping_timeout=2).ping()
    assert ok and detail == "ok"
    ok, detail = OllamaClient(f"http://127.0.0.1:{unused_port}", "test:1b", ping_timeout=0.5).ping()
    assert not ok
    assert "cannot reach Ollama" in detail


def test_list_models(server):
    client = OllamaClient(server, "test:1b", ping_timeout=2)
    assert client.list_models() == ["test:1b"]


def test_malformed_tool_entries_ignored(server):
    _STATE["reply"] = {
        "message": {
            "content": "hmm",
            "tool_calls": ["garbage", {"function": "not-a-dict"}, {"function": {"name": "ok"}}],
        }
    }
    client = OllamaClient(server, "test:1b")
    resp = client.chat([])
    assert resp.tool_calls == [{"name": "ok", "arguments": None}]
    assert resp.content == "hmm"
