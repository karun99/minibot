"""Terminal UI (FR-6): status panel with bot sprite + slash commands + chat.

Pure stdlib: ANSI colours (with NO_COLOR / non-TTY fallback), VT enabled on
Windows 10+. Works in Termux, Linux, macOS and Windows terminals.
"""

from __future__ import annotations

import os
import sys
import time

from . import __version__
from .checks import collect_checks, print_checks
from .needle import make_needle
from .ollama_client import OllamaError, OllamaUnreachable
from .orchestrator import Orchestrator

MOODS = ("happy", "content", "sleepy", "starving", "sad", "sick")

_EYES = {"happy": "^ ^", "content": "o o", "sleepy": "- -", "starving": "o o", "sad": "o o", "sick": "x x"}
_MOUTH = {"happy": "w", "content": "-", "sleepy": "u", "starving": "D", "sad": "(", "sick": "~"}

_STYLES = {
    "reset": "0", "bold": "1", "dim": "2",
    "red": "31", "green": "32", "yellow": "33", "blue": "34",
    "magenta": "35", "cyan": "36", "white": "37",
}

HELP = """\
Commands (everything else is chat for the bot):
  /help              show this help           /status | /st   detailed status
  /feed              feed the bot             /play           play with the bot
  /rest              let the bot recharge     /treat          give a treat from the bag
  /ls [dir]          list sandbox files       /read <file>    read a sandbox file
  /write <file>      write one (end with '.' line)
  /stats [n]         recent stat history chart
  /history [n]       recent sandbox writes    /undo [file]    revert a write
  /needle [on|off]   toggle the pre-filter    /doctor         run health checks
  /clear             clear screen + history   /quit | /q      exit (also Ctrl-D)
Tip: caring for the bot (feed/play/rest) earns treats; /treat spends one."""


class _Quit(Exception):
    pass


def _unicode_ok() -> bool:
    if os.environ.get("MINIBOT_ASCII"):
        return False
    enc = getattr(sys.stdout, "encoding", None) or ""
    return "utf" in enc.lower()


def _enable_windows_vt() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(handle, ctypes.byref(mode)):
            k32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:  # noqa: BLE001 - old consoles just stay plain
        pass


class Palette:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def paint(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        codes = [_STYLES[s] for s in styles if s in _STYLES]
        return "\x1b[" + ";".join(codes) + "m" + text + "\x1b[0m"


class TerminalUI:
    def __init__(self, config, store, sandbox, toolset, client, orchestrator: Orchestrator, logger=None):
        self.config = config
        self.store = store
        self.sandbox = sandbox
        self.toolset = toolset
        self.client = client
        self.orchestrator = orchestrator
        self.logger = logger
        self.unicode = _unicode_ok()
        self.pal = Palette(sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
        self._ctrl_c = False

    # -- rendering -----------------------------------------------------------

    def _sprite(self, mood: str) -> list[str]:
        mood = mood if mood in MOODS else "content"
        lines = [
            "  .---.",
            f" / {_EYES[mood]} \\",
            f" \\  {_MOUTH[mood]}  /",
            "  '---'",
        ]
        if mood in ("sleepy", "sick"):
            lines[3] = "  '---' zZ" if not self.unicode else "  '---' 💤"
        return lines

    def _bar(self, value: float, width: int = 10) -> str:
        filled = max(0, min(width, int(round(value / 10.0 * width))))
        if self.unicode:
            return "[" + "█" * filled + "░" * (width - filled) + "]"
        return "[" + "#" * filled + "-" * (width - filled) + "]"

    def _stat_color(self, kind: str, value: float) -> str:
        if kind == "health":
            return "red" if value < 40 else "green"
        if kind == "hunger":
            return "red" if value >= 85 else "yellow" if value >= 60 else "green"
        return "red" if value <= 15 else "yellow" if value <= 30 else "green"

    def render_status(self, detailed: bool = False) -> None:
        self.store.tick()
        st = self.store.status()
        mood = st["mood"]
        sprite = self._sprite(mood)
        pad = max(len(line) for line in sprite) + 2
        rows = [
            ("Hunger", "hunger", st["hunger"]),
            ("Happiness", "happiness", st["happiness"]),
            ("Energy", "energy", st["energy"]),
            ("Health", "health", st["health"]),
        ]
        header = (
            f"{st['name']} the bot · mood: {mood} · age {st['age_days']}d · treats {st.get('treats', 0)}"
            if self.unicode
            else f"{st['name']} the bot - mood: {mood} - age {st['age_days']}d - treats {st.get('treats', 0)}"
        )
        print(self.pal.paint(f"  {header}", "bold"))
        for i, (label, kind, value) in enumerate(rows):
            art = sprite[i] if i < len(sprite) else " " * pad
            bar_line = (
                f"{label:<10} {self._bar(value)} {value:>3}/100"
            )
            print(f"  {self.pal.paint(art.ljust(pad), 'cyan')}"
                  f" {self.pal.paint(bar_line.ljust(28), self._stat_color(kind, value))}")
        alerts = []
        if st["health"] < 40:
            alerts.append(f"{st['name']} is sick — rest and feed often!")
        if st["hunger"] >= 85:
            alerts.append(f"{st['name']} is starving — feed them!")
        if st["energy"] <= 0:
            alerts.append(f"{st['name']} is exhausted — /rest!")
        for alert in alerts:
            print(self.pal.paint(f"  ⚠ {alert}" if self.unicode else f"  ! {alert}", "red", "bold"))
        if detailed:
            print(self.pal.paint(
                f"  state: {self.config.state_path}\n"
                f"  sandbox: {self.sandbox.root}\n"
                f"  model: {self.config.model} @ {self.config.ollama_url}", "dim"))

    def _banner(self) -> None:
        line = f"minibot v{__version__} · config: {self.config.source}"
        print(self.pal.paint(line, "dim"))
        print(self.pal.paint(
            f"  bot: {self.store.state.name} · sandbox: {self.sandbox.root}", "dim"))
        needle = self.config.needle
        state = f"on ({needle.mode})" if needle.enabled else "off"
        print(self.pal.paint(f"  model: {self.config.model} · needle: {state} · /help for commands", "dim"))
        print()

    def _prompt(self) -> str:
        return self.pal.paint("❯ ", "bold", "magenta") if self.unicode else self.pal.paint("> ", "bold", "magenta")

    # -- chat ----------------------------------------------------------------

    def _chat(self, line: str) -> None:
        print(self.pal.paint(f"you: {line}", "bold"))
        try:
            turn = self.orchestrator.handle(line)
        except OllamaUnreachable as e:
            print(self.pal.paint(f"⚠ Ollama unreachable — {e}", "red"))
            print(self.pal.paint("  start it with `ollama serve`, or check ollama_url in config.json", "dim"))
            return
        except OllamaError as e:
            print(self.pal.paint(f"⚠ Ollama error — {e}", "red"))
            return
        if turn.tools_run:
            ran = ", ".join(turn.tools_run)
            prefix = "» (needle) " if turn.route == "direct" else "» tools: "
            print(self.pal.paint(f"{prefix}{ran}", "cyan"))
        name = self.store.state.name
        print(self.pal.paint(f"{name}: {turn.text}", "green"))
        print()

    # -- direct commands -----------------------------------------------------

    def _run_tool(self, name: str, args: dict | None = None) -> dict:
        outcome = self.toolset.execute(name, args or {})
        if outcome.get("ok"):
            if outcome.get("message"):
                print(self.pal.paint(f"» {name}: {outcome['message']}", "cyan"))
            else:
                print(self.pal.paint(f"» {name}: ok", "cyan"))
        else:
            print(self.pal.paint(f"✖ {name}: {outcome.get('error', 'failed')}", "red"))
        return outcome

    def _cmd_ls(self, arg: str) -> None:
        outcome = self._run_tool("list_dir", {"path": arg.strip() or "."})
        for entry in outcome.get("entries", []):
            if entry.get("type") == "dir":
                print(f"  {self.pal.paint(entry['name'], 'blue')}")  # name ends with "/"
            else:
                size = entry.get("size", 0)
                print(f"  {entry['name']:<38} {size:>8} B")
        if outcome.get("note"):
            print(self.pal.paint(f"  ({outcome['note']})", "dim"))

    def _cmd_read(self, arg: str) -> None:
        path = arg.strip()
        if not path:
            print(self.pal.paint("usage: /read <path>", "yellow"))
            return
        outcome = self.toolset.execute("read_file", {"path": path})
        if not outcome.get("ok"):
            print(self.pal.paint(f"✖ read_file: {outcome.get('error', 'failed')}", "red"))
            return
        content = outcome.get("content", "")
        print(self.pal.paint(f"── {outcome['path']} ({outcome['size']} B) ──", "dim"))
        lines = content.splitlines()
        shown = lines[:200]
        print("\n".join(shown))
        if len(lines) > len(shown):
            print(self.pal.paint(f"... [{len(lines) - len(shown)} more lines]", "dim"))

    def _cmd_write(self, arg: str) -> None:
        path = arg.strip()
        if not path:
            print(self.pal.paint("usage: /write <path>  (type content, end with a single '.' line)", "yellow"))
            return
        print(self.pal.paint("enter content; finish with a single '.' line (Ctrl-C cancels):", "dim"))
        lines: list[str] = []
        try:
            while True:
                line = input()
                if line.strip() == ".":
                    break
                lines.append(line)
        except (KeyboardInterrupt, EOFError):
            print(self.pal.paint("aborted.", "yellow"))
            return
        content = "\n".join(lines) + ("\n" if lines else "")
        self._run_tool("write_file", {"path": path, "content": content})

    def _cmd_needle(self, arg: str) -> None:
        value = arg.strip().lower()
        if value in ("on", "off"):
            self.config.needle.enabled = value == "on"
        nc = self.config.needle
        state = f"on (mode={nc.mode})" if nc.enabled else "off"
        print(f"needle pre-filter: {state}")
        self.orchestrator.set_needle(make_needle(self.config, self.client, self.logger))

    def _cmd_doctor(self) -> None:
        checks = collect_checks(self.config, self.client, self.sandbox, self.store)
        print(self.pal.paint("health checks:", "bold"))
        print_checks(checks, emit=print, unicode_ok=self.unicode)

    # -- history / stats / undo ---------------------------------------------

    _SPARK_GLYPHS = {"hunger": ("█", "#"), "happiness": ("+", "+"), "energy": ("e", "e"), "health": ("♥", "H")}

    def _spark(self, value: float, key: str, width: int = 3) -> str:
        glyph = self._SPARK_GLYPHS[key][0 if self.unicode else 1]
        level = max(0, min(width, int(round(float(value) / 100.0 * width))))
        return glyph * level + "·" * (width - level)

    def _cmd_stats(self, arg: str) -> None:
        self.store.tick()
        limit = int(arg.strip()) if arg.strip().isdigit() else 40
        samples = self.store.stats_history(max(1, limit))
        if not samples:
            self.store.record_sample()
            self.store.save()
            samples = self.store.stats_history(max(1, limit))
        st = self.store.status()
        print(self.pal.paint(
            f"{st['name']} — last {len(samples)} samples · treats: {st.get('treats', 0)}", "bold"))
        if not samples:
            print(self.pal.paint("  (no samples yet — the chart fills in as you interact)", "dim"))
            return
        for key in ("hunger", "happiness", "energy", "health"):
            bar = "".join(self._spark(sample[key], key) for sample in samples)
            print(f"  {key:<9} {bar} {st[key]:>3}")
        first, last = samples[0], samples[-1]
        arrow = self.unicode and "→" or "->"
        print(self.pal.paint(
            f"  trend: hunger {first['hunger']:.0f}{arrow}{last['hunger']:.0f}  "
            f"happiness {first['happiness']:.0f}{arrow}{last['happiness']:.0f}  "
            f"energy {first['energy']:.0f}{arrow}{last['energy']:.0f}  "
            f"health {first['health']:.0f}{arrow}{last['health']:.0f}", "dim"))

    def _cmd_history(self, arg: str) -> None:
        limit = int(arg.strip()) if arg.strip().isdigit() else 20
        outcome = self.toolset.execute("file_history", {"limit": max(1, limit)})
        if not outcome.get("ok"):
            print(self.pal.paint(f"✖ file_history: {outcome.get('error', 'failed')}", "red"))
            return
        entries = outcome.get("entries", [])
        if not entries:
            print(self.pal.paint("no recorded writes yet", "dim"))
            return
        for entry in entries:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.get("t", 0)))
            mark = "" if entry.get("undoable", True) else "  (not undoable)"
            print(f"  {stamp}  {entry.get('action', 'write'):<9} "
                  f"{entry.get('path')} ({entry.get('bytes', 0)} B){mark}")
        if outcome.get("total", 0) > outcome.get("count", 0):
            print(self.pal.paint(f"  ({outcome['count']} of {outcome['total']} recorded writes)", "dim"))

    def _cmd_undo(self, arg: str) -> None:
        path = arg.strip()
        self._run_tool("undo_write", {"path": path} if path else {})

    # -- command loop --------------------------------------------------------

    def run(self) -> int:
        _enable_windows_vt()
        self._banner()
        while True:
            self.render_status()
            try:
                line = input(self._prompt())
            except (KeyboardInterrupt, EOFError):
                if self._ctrl_c:
                    print()
                    return 0
                self._ctrl_c = True
                print(self.pal.paint("\n(Ctrl-C again to exit, /quit to quit)", "dim"))
                continue
            self._ctrl_c = False
            line = line.strip()
            if not line:
                print()
                continue
            try:
                if line.startswith("/"):
                    self._command(line)
                else:
                    print()
                    self._chat(line)
            except _Quit:
                print(self.pal.paint("bye!", "cyan"))
                return 0

    def _command(self, line: str) -> None:
        parts = line[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        print()
        if cmd in ("quit", "q", "exit"):
            raise _Quit()
        if cmd in ("help", "h", "?"):
            print(HELP)
        elif cmd in ("status", "st"):
            self.render_status(detailed=True)
        elif cmd == "feed":
            self._run_tool("feed_bot")
        elif cmd == "play":
            self._run_tool("play_with_bot")
        elif cmd == "rest":
            self._run_tool("rest_bot")
        elif cmd == "treat":
            self._run_tool("give_treat")
        elif cmd == "stats":
            self._cmd_stats(arg)
        elif cmd in ("history", "hist"):
            self._cmd_history(arg)
        elif cmd in ("undo", "revert"):
            self._cmd_undo(arg)
        elif cmd in ("ls", "dir"):
            self._cmd_ls(arg)
        elif cmd in ("read", "cat"):
            self._cmd_read(arg)
        elif cmd == "write":
            self._cmd_write(arg)
        elif cmd == "needle":
            self._cmd_needle(arg)
        elif cmd == "doctor":
            self._cmd_doctor()
        elif cmd == "clear":
            self.orchestrator.reset()
            print("\x1b[2J\x1b[H" if self.unicode or sys.stdout.isatty() else "\n" * 3)
        else:
            print(self.pal.paint(f"unknown command /{cmd} — try /help", "yellow"))
