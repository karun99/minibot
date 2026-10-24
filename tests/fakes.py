"""Test doubles: a scriptable fake Ollama client and canned responses."""

from __future__ import annotations

from minibot.ollama_client import ChatResponse


def text_resp(text: str) -> ChatResponse:
    return ChatResponse(content=text)


def tool_call_resp(name: str, arguments=None) -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[{"name": name, "arguments": {} if arguments is None else arguments}],
    )


def multi_call_resp(calls: list[tuple[str, dict]]) -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[{"name": name, "arguments": args} for name, args in calls],
    )


class FakeClient:
    """Returns scripted responses in order; records every call."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def chat(self, messages, tools=None, model=None, temperature=None):
        self.calls.append(
            {
                "messages": [dict(m) for m in messages],
                "tools": tools,
                "model": model,
                "temperature": temperature,
            }
        )
        if not self.responses:
            raise AssertionError("FakeClient exhausted: no scripted response left")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def ping(self):
        return True, "ok"

    def list_models(self):
        return ["fake:1b"]
