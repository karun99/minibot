"""MCP server tests (`minibot mcp`): stdio JSON-RPC protocol behaviour."""

import io
import json

import pytest

from minibot.mcp_server import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    MCPServer,
)
from minibot.sandbox import Sandbox
from minibot.tools import build_toolset


@pytest.fixture
def mcp(store, tmp_path):
    sandbox = Sandbox(tmp_path / "sbx", journal_path=tmp_path / "j.jsonl")
    return MCPServer(build_toolset(store, sandbox), logger=None)


def call(server, method, params=None, request_id=1):
    return server.handle(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )


def test_initialize_handshake(mcp):
    response = call(mcp, "initialize", {"protocolVersion": "2024-11-05"})
    result = response["result"]
    assert result["protocolVersion"]
    assert result["serverInfo"]["name"] == "minibot"
    assert "tools" in result["capabilities"]


def test_initialized_notification_has_no_reply(mcp):
    message = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert mcp.handle(message) is None
    assert mcp.initialized is True


def test_ping(mcp):
    assert call(mcp, "ping")["result"] == {}


def test_tools_list_exposes_every_tool(mcp):
    tools = call(mcp, "tools/list")["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {"feed_bot", "read_file", "write_file", "give_treat", "undo_write"} <= names
    for tool in tools:
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object"


def test_tools_call_feed_bot(mcp):
    response = call(mcp, "tools/call", {"name": "feed_bot", "arguments": {}})
    assert response["result"]["isError"] is False
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["status"]["hunger"] < 100


def test_tools_call_unknown_tool_is_an_error(mcp):
    response = call(mcp, "tools/call", {"name": "launch_missiles", "arguments": {}})
    assert response["result"]["isError"] is True
    assert "unknown tool" in response["result"]["content"][0]["text"]


def test_tools_call_file_roundtrip_and_undo(mcp):
    call(mcp, "tools/call", {"name": "write_file",
                             "arguments": {"path": "a.txt", "content": "one"}})
    call(mcp, "tools/call", {"name": "write_file",
                             "arguments": {"path": "a.txt", "content": "two"}})
    undone = call(mcp, "tools/call", {"name": "undo_write", "arguments": {"path": "a.txt"}})
    assert undone["result"]["isError"] is False
    read = call(mcp, "tools/call", {"name": "read_file", "arguments": {"path": "a.txt"}})
    payload = json.loads(read["result"]["content"][0]["text"])
    assert payload["content"] == "one"


def test_tools_call_rejects_sandbox_escape(mcp):
    response = call(mcp, "tools/call",
                    {"name": "read_file", "arguments": {"path": "../../etc/passwd"}})
    assert response["result"]["isError"] is True
    assert "outside the sandbox" in response["result"]["content"][0]["text"]


def test_tools_call_missing_name(mcp):
    response = call(mcp, "tools/call", {"arguments": {}})
    assert response["error"]["code"] == INVALID_PARAMS


def test_unknown_method(mcp):
    response = call(mcp, "resources/list")
    assert response["error"]["code"] == METHOD_NOT_FOUND


def test_non_object_request(mcp):
    assert mcp.handle(["not", "an", "object"])["error"]["code"]


def test_serve_speaks_newline_json(mcp):
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
    )
    stdout = io.StringIO()
    assert mcp.serve(stdin, stdout) == 0
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [line["id"] for line in lines] == [1, 2]
    assert lines[1]["result"]["tools"]


def test_serve_reports_parse_errors(mcp):
    stdin = io.StringIO("{not json}\n")
    stdout = io.StringIO()
    mcp.serve(stdin, stdout)
    response = json.loads(stdout.getvalue().splitlines()[0])
    assert response["error"]["code"] == PARSE_ERROR
