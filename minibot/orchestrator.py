"""ReAct orchestrator (FR-2): model -> tool call -> execute -> feed back.

Terminates on a final text answer or after max_iterations (default 5).
Malformed tool JSON gets exactly one retry before a graceful error (NFR-2).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .ollama_client import OllamaClient, OllamaError
from .sandbox import Sandbox
from .state import BotStore
from .tools import Toolset

log = logging.getLogger("minibot.orchestrator")


@dataclass
class TurnResult:
    text: str
    tools_run: list[str] = field(default_factory=list)
    route: str = "loop"  # "loop" | "direct" (needle shortcut)
    iterations: int = 0
    needle_label: str | None = None


def system_prompt(bot_name: str, sandbox_root: Path) -> str:
    sandbox_label = sandbox_root.name or str(sandbox_root)
    return f"""You are the personality of a virtual terminal bot named "{bot_name}". Users type natural-language messages and you handle them.

Tools — call them instead of pretending (never claim an action you didn't perform):
- feed_bot() / play_with_bot() / rest_bot() / get_bot_status() — care for {bot_name}.
- read_file{{path}} / write_file{{path,content}} / list_dir{{path}} — files inside the sandbox directory "{sandbox_label}". Paths are relative to that root; absolute paths and ".." are rejected by the sandbox.

Behaviour:
- Decide whether the message needs a tool. Bot care or file work -> call the needed tool(s), then reply in 1-3 short friendly sentences quoting the real result (numbers/paths from the tool output).
- Casual chat -> just reply, no tool. Keep replies light and concise (max ~60 words), in the bot's voice.
- Multiple requests ("feed and read notes.md") -> call both tools in one turn when possible.
- If a tool returns ok=false, say plainly what went wrong; never claim success without a tool result.
- Never invent file contents; never suggest or attempt paths outside the sandbox.
- Reply in the user's language. Plain text only: no markdown headers, no code fences around normal replies."""


class Orchestrator:
    def __init__(
        self,
        config,
        store: BotStore,
        sandbox: Sandbox,
        toolset: Toolset,
        client: OllamaClient,
        needle=None,
        logger: logging.Logger | None = None,
    ):
        self.config = config
        self.store = store
        self.sandbox = sandbox
        self.toolset = toolset
        self.client = client
        self.needle = needle
        self.logger = logger or log
        self.history: list[dict] = []

    # -- history -------------------------------------------------------------

    def reset(self) -> None:
        self.history.clear()

    def set_needle(self, needle) -> None:
        self.needle = needle

    def _trim(self, messages: list[dict]) -> list[dict]:
        """Keep the most recent history_user_turns user turns (tool pairs intact)."""
        n = self.config.history_user_turns
        idxs = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        if len(idxs) <= n:
            return messages
        return messages[idxs[len(idxs) - n] :]

    def _commit(self, user_text: str, generated: list[dict]) -> None:
        self.history = self._trim(self.history + [{"role": "user", "content": user_text}] + generated)

    def _messages_for(self, user_text: str) -> list[dict]:
        return (
            [{"role": "system", "content": system_prompt(self.store.state.name, self.sandbox.root)}]
            + self._trim(self.history)
            + [{"role": "user", "content": user_text}]
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _parse_call(call: dict) -> tuple[str, dict, bool]:
        """Return (name, args, malformed)."""
        name = call.get("name") or ""
        if not name:
            return "", {}, True
        args = call.get("arguments")
        if args is None or args == "":
            return name, {}, False
        if isinstance(args, dict):
            return name, args, False
        if isinstance(args, str):
            try:
                parsed = json.loads(args.strip() or "{}")
            except json.JSONDecodeError:
                return name, {}, True
            if isinstance(parsed, dict):
                return name, parsed, False
        return name, {}, True

    def _clip(self, text: str) -> str:
        limit = self.config.max_tool_output_chars
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n...[truncated {len(text) - limit} characters]"

    def _text_tool_call(self, content: str) -> dict | None:
        """Small models sometimes emit tool JSON as plain/fenced text instead of a
        native tool_call. If the text names a known tool, treat it as one."""
        s = (content or "").strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
        if fence:
            s = fence.group(1).strip()
        if not (s.startswith("{") and s.endswith("}")):
            return None
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        name = str(data.get("name") or "")
        if not self.toolset.has(name):
            return None
        args = data.get("arguments") or data.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        self.logger.info("text_tool_call fallback: %s", name)
        return {"name": name, "arguments": args}

    # -- main entry ----------------------------------------------------------

    def handle(self, user_text: str) -> TurnResult:
        text = (user_text or "").strip()
        if not text:
            return TurnResult(text="")
        self.store.tick()

        needle_label = None
        if self.needle is not None:
            try:
                c = self.needle.classify(text)
            except Exception:  # noqa: BLE001 - pre-filter must never break a turn
                self.logger.exception("needle classification failed")
                c = None
            if c is not None:
                needle_label = c.label
                if (
                    c.label == "pet_action"
                    and c.tool
                    and c.confidence >= self.config.needle.min_confidence
                ):
                    outcome = self.toolset.execute(c.tool, {})
                    msg = outcome.get("message") or outcome.get("error") or json.dumps(outcome)
                    turn = TurnResult(
                        text=str(msg),
                        tools_run=[c.tool] if outcome.get("ok") else [],
                        route="direct",
                        needle_label=needle_label,
                    )
                    self._commit(text, [{"role": "assistant", "content": turn.text}])
                    return turn

        return self._loop(text, needle_label)

    def _loop(self, text: str, needle_label: str | None) -> TurnResult:
        messages = self._messages_for(text)
        generated: list[dict] = []
        tools_run: list[str] = []
        malformed_seen = False
        max_iter = self.config.max_iterations

        used_text_fallback = False
        for iteration in range(1, max_iter + 1):
            resp = self.client.chat(messages, tools=self.toolset.schemas())
            calls = list(resp.tool_calls)
            if not calls and not used_text_fallback:
                pseudo = self._text_tool_call(resp.content)
                if pseudo is not None:
                    used_text_fallback = True
                    calls = [pseudo]
            assistant_msg: dict = {"role": "assistant", "content": resp.content}
            if calls:
                assistant_msg["tool_calls"] = [
                    {"function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in calls
                ]
            messages.append(assistant_msg)
            generated.append(assistant_msg)

            if not calls:
                final = resp.content.strip() or "(the model returned an empty response)"
                self._commit(text, generated)
                return TurnResult(
                    text=final, tools_run=tools_run, route="loop",
                    iterations=iteration, needle_label=needle_label,
                )

            for call in calls:
                name, args, bad = self._parse_call(call)
                if bad:
                    if not malformed_seen:
                        malformed_seen = True
                        note = {
                            "role": "tool",
                            "name": name or "unknown",
                            "content": json.dumps({
                                "ok": False,
                                "error": "Malformed tool call: 'name' and JSON-object "
                                         "'arguments' are required. Retry once with valid JSON.",
                            }),
                        }
                        messages.append(note)
                        generated.append(note)
                        continue
                    self._commit(text, generated)
                    return TurnResult(
                        text=(
                            "That tool call was malformed twice, so I stopped instead of "
                            "looping. Try rephrasing, or use a direct /command."
                        ),
                        tools_run=tools_run, route="loop",
                        iterations=iteration, needle_label=needle_label,
                    )
                if self.toolset.has(name):
                    outcome = self.toolset.execute(name, args)
                else:
                    outcome = {
                        "ok": False,
                        "error": f"unknown tool '{name}'",
                        "available": self.toolset.names(),
                    }
                if outcome.get("ok") and name not in tools_run:
                    tools_run.append(name)
                note = {"role": "tool", "name": name, "content": self._clip(json.dumps(outcome))}
                messages.append(note)
                generated.append(note)

        self._commit(text, generated)
        return TurnResult(
            text=(
                f"I hit the limit of {max_iter} tool steps without finishing. "
                "Try a simpler request, or use a direct /command."
            ),
            tools_run=tools_run, route="loop",
            iterations=max_iter, needle_label=needle_label,
        )
