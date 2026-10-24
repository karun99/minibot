"""Optional intent pre-filter (FR-5).

Two backends, both fully offline-capable:
  * HeuristicNeedle — zero-model keyword/regex classifier (tiny, instant).
  * OllamaNeedle    — tiny classifier model hosted by Ollama; degrades to the
                      heuristic on any failure (FR-5.3 graceful degradation).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

log = logging.getLogger("minibot.needle")

LABELS = ("pet_action", "file_action", "chat", "unknown")
PET_TOOLS = ("feed_bot", "play_with_bot", "rest_bot", "get_bot_status")


@dataclass
class Classification:
    label: str
    confidence: float
    tool: str | None = None
    detail: str = ""


# Strong, unambiguous bot-care intents (safe to execute without the LLM).
_PET_STRONG: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"^\s*(feed|eat)\b", re.I), "feed_bot", 0.98),
    (re.compile(r"^\s*(play|let'?s play)\b", re.I), "play_with_bot", 0.98),
    (re.compile(r"^\s*(rest|sleep|nap|go to sleep)\b", re.I), "rest_bot", 0.98),
    (re.compile(r"^\s*(status|check (the )?(status|stats))\b", re.I), "get_bot_status", 0.98),
    (re.compile(
        r"\b(feed)\s+(my|the|our)?\s*(bot|robot|creature|it|him|her)\b", re.I
    ), "feed_bot", 0.96),
    (re.compile(
        r"\b(play with)\s+(my|the|our)?\s*(bot|robot|creature|it|him|her)\b", re.I
    ), "play_with_bot", 0.96),
    (re.compile(
        r"\b(let|have)\s+(my|the|our)?\s*(bot|it|him|her)\s+(rest|sleep|nap)\b", re.I
    ), "rest_bot", 0.95),
    (re.compile(r"\b(bot|he|she|it)\s+is\s+(so\s+)?hungry\b", re.I), "feed_bot", 0.95),
    (re.compile(r"\b(bot|he|she|it)\s+is\s+(so\s+)?(tired|sleepy)\b", re.I), "rest_bot", 0.95),
    (re.compile(r"\b(how is|how's)\s+(my|the|our)?\s*(bot|it|he|she)\b", re.I), "get_bot_status", 0.95),
]

# Vague bot-care signals: reported as pet_action but below the direct-exec
# threshold, so they take the normal (correct) path through the LLM loop.
_PET_WEAK: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"\b(feed|hungry|hunger|eat|snack|meal)\b", re.I), "feed_bot", 0.85),
    (re.compile(r"\b(play|toy|games?|fetch)\b", re.I), "play_with_bot", 0.85),
    (re.compile(r"\b(rest|sleep|nap|sleepy|tired)\b", re.I), "rest_bot", 0.85),
    (re.compile(r"\b(status|mood|stats)\b", re.I), "get_bot_status", 0.85),
]

_CODE_EXT = (
    r"py|js|ts|jsx|tsx|json|md|txt|sh|bash|c|h|cpp|hpp|java|html|css|scss|"
    r"yaml|yml|toml|cfg|ini|log|csv|xml|rs|go|rb|php|sql"
)
_FILE_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(rf"\b(read|open|cat|type|view|show)\b[^.]*\.({_CODE_EXT})\b", re.I), 0.95),
    (re.compile(r"\b(read|open|cat|view)\s+(the\s+)?(file|config|source|script)\b", re.I), 0.95),
    (re.compile(
        r"\b(write|create|save|edit|update|overwrite|modify|make)\b[^.]*\b(file|script|module|code)\b",
        re.I,
    ), 0.95),
    (re.compile(rf"\b(write|create|make)\s+\S+\.({_CODE_EXT})\b", re.I), 0.96),
    (re.compile(r"^\s*(ls|dir|ll)\b", re.I), 0.95),
    (re.compile(r"\b(list|show|print|what)\b[^.]*\b(files|folders|director(y|ies)|dir)\b", re.I), 0.92),
    (re.compile(r"\.\.[\\/]|\.\.\s*$"), 0.95),  # traversal attempts must reach the sandbox
    (re.compile(rf"^\s*\S+\.({_CODE_EXT})\s*$", re.I), 0.9),
]

_CHAT_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"^\s*(hi|hello|hey|yo|hiya|howdy|good (morning|afternoon|evening))\b", re.I), 0.85),
    (re.compile(r"^\s*(thanks|thank you|ty|bye|goodbye|see ya)\b", re.I), 0.85),
]

_NEGATION = re.compile(r"\b(don'?t|do not|never|stop|avoid|not)\b", re.I)


class HeuristicNeedle:
    """Regex/keyword classifier: no model, no network, sub-millisecond."""

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or log

    def classify(self, text: str) -> Classification:
        s = (text or "").strip()
        if not s:
            return Classification("unknown", 0.0, detail="empty input")

        file_hit = max((conf for pat, conf in _FILE_PATTERNS if pat.search(s)), default=0.0)

        strong = [(tool, conf) for pat, tool, conf in _PET_STRONG if pat.search(s)]
        weak = [(tool, conf) for pat, tool, conf in _PET_WEAK if pat.search(s)]
        pet_tool: str | None = None
        pet_conf = 0.0
        if strong:
            tools_seen = {tool for tool, _ in strong}
            if len(tools_seen) == 1:
                pet_tool, pet_conf = max(strong, key=lambda item: item[1])
            else:
                pet_conf = 0.6  # several pet intents: let the LLM decide
        elif weak:
            tools_seen = {tool for tool, _ in weak}
            if len(tools_seen) == 1:
                pet_tool, pet_conf = weak[0]
            else:
                pet_conf = 0.6

        if pet_conf > 0:
            if file_hit:
                # Mixed intent ("feed and read x.py"): never direct-execute.
                pet_tool = None
                pet_conf = min(pet_conf, 0.5)
            if _NEGATION.search(s):
                pet_conf = max(0.0, pet_conf - 0.35)
            self.logger.debug("needle: %r -> pet_action %.2f", s[:60], pet_conf)
            return Classification("pet_action", round(min(1.0, pet_conf), 2), tool=pet_tool)

        if file_hit:
            self.logger.debug("needle: %r -> file_action %.2f", s[:60], file_hit)
            return Classification("file_action", round(file_hit, 2))

        for pat, conf in _CHAT_PATTERNS:
            if pat.search(s):
                return Classification("chat", conf, detail="greeting/social")
        return Classification("unknown", 0.0)


_CLASSIFY_SYSTEM = (
    'You are a tiny intent classifier. Classify the user message into exactly one label: '
    'pet_action (care for a virtual bot: feed/play/rest/status), file_action (read/write/list '
    'files), chat (small talk or anything else), unknown. '
    'Reply with ONLY a JSON object like {"label":"pet_action","confidence":0.95,'
    '"tool":"feed_bot"} — tool must be one of feed_bot, play_with_bot, rest_bot, get_bot_status '
    'or an empty string.'
)


class OllamaNeedle:
    """Classifier backed by a small Ollama model; falls back to the heuristic."""

    def __init__(self, client, model: str, fallback: "HeuristicNeedle | None" = None,
                 logger: logging.Logger | None = None):
        self.client = client
        self.model = model
        self.fallback = fallback or HeuristicNeedle(logger)
        self.logger = logger or log

    def classify(self, text: str) -> Classification:
        messages = [
            {"role": "system", "content": _CLASSIFY_SYSTEM},
            {"role": "user", "content": text or ""},
        ]
        try:
            resp = self.client.chat(messages, tools=None, model=self.model, temperature=0.0)
            data = self._parse(resp.content)
            label = data.get("label")
            if label not in LABELS:
                raise ValueError(f"bad label {label!r}")
            conf = float(data.get("confidence", 0.8))
            tool = data.get("tool")
            tool = tool if tool in PET_TOOLS else None
            return Classification(label, max(0.0, min(1.0, conf)), tool=tool, detail="ollama")
        except Exception as e:  # noqa: BLE001 - pre-filter must never break the app
            self.logger.warning("needle model failed (%s) — using heuristic fallback", e)
            return self.fallback.classify(text)

    @staticmethod
    def _parse(content: str) -> dict:
        s = (content or "").strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
        if fence:
            s = fence.group(1).strip()
        if not s.startswith("{"):
            start, end = s.find("{"), s.rfind("}")
            if start == -1 or end <= start:
                raise ValueError(f"no JSON object in reply: {content[:80]!r}")
            s = s[start : end + 1]
        data = json.loads(s)
        if not isinstance(data, dict):
            raise ValueError("classifier reply is not a JSON object")
        return data


def make_needle(config, client, logger: logging.Logger | None = None):
    """Build the configured pre-filter, or None when disabled (FR-5.3)."""
    nc = config.needle
    if not nc.enabled:
        return None
    heuristic = HeuristicNeedle(logger)
    if nc.mode == "ollama":
        return OllamaNeedle(client, nc.model, fallback=heuristic, logger=logger)
    return heuristic
