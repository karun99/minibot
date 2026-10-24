"""CLI entry point: run the terminal bot, or script one-shot actions."""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import __version__
from .checks import collect_checks, print_checks
from .config import BotConfig, ConfigError, load_config
from .logger import setup_logging
from .needle import make_needle
from .ollama_client import OllamaClient, OllamaError, OllamaUnreachable
from .orchestrator import Orchestrator
from .sandbox import Sandbox, SandboxError
from .state import BotError, BotStore
from .tools import ToolError, build_toolset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minibot",
        description="A locally-hosted bot for your terminal, powered by Ollama tool calling.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", metavar="PATH", help="path to config.json")
    parser.add_argument("--model", help="override the Ollama model name")
    parser.add_argument("--sandbox", help="override the sandbox directory")
    parser.add_argument("--ollama-url", help="override the Ollama URL")
    parser.add_argument("--no-needle", action="store_true", help="disable the intent pre-filter")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="start the interactive terminal bot (default)")
    run_p.add_argument("--offline", action="store_true",
                       help="start even if Ollama is unreachable (direct /commands only)")

    status_p = sub.add_parser("status", help="show bot status")
    status_p.add_argument("--json", action="store_true", help="machine-readable output")
    sub.add_parser("feed", help="feed the bot once")
    sub.add_parser("play", help="play with the bot once")
    sub.add_parser("rest", help="let the bot recharge once")
    ls_p = sub.add_parser("ls", help="list a sandbox directory")
    ls_p.add_argument("path", nargs="?", default=".")
    read_p = sub.add_parser("read", help="read a sandbox file")
    read_p.add_argument("path")
    write_p = sub.add_parser("write", help="write a sandbox file (content from stdin)")
    write_p.add_argument("path")
    chat_p = sub.add_parser("chat", help="one-shot chat turn (scriptable)")
    chat_p.add_argument("message", nargs="+")
    sub.add_parser("doctor", help="run environment health checks")
    sub.add_parser("treat", help="give the bot a treat from the inventory")
    stats_p = sub.add_parser("stats", help="show the recent stat history chart")
    stats_p.add_argument("--json", action="store_true", help="machine-readable output")
    stats_p.add_argument("--points", type=int, default=40, help="how many samples to chart")
    hist_p = sub.add_parser("history", help="list recent sandbox file writes")
    hist_p.add_argument("--limit", type=int, default=20)
    undo_p = sub.add_parser("undo", help="revert a sandbox file write")
    undo_p.add_argument("path", nargs="?", help="file to revert (default: last write)")
    sub.add_parser("mcp", help="serve the bot tools over stdio as an MCP server")
    return parser


def _overrides(args: argparse.Namespace) -> dict:
    return {
        "model": args.model,
        "sandbox_path": args.sandbox,
        "ollama_url": args.ollama_url,
        "needle.enabled": False if args.no_needle else None,
    }


def _sandbox(config: BotConfig) -> Sandbox:
    """Sandbox with the write journal attached (enables /history and /undo)."""
    return Sandbox(
        config.sandbox_path,
        max_file_bytes=config.max_file_bytes,
        journal_path=config.journal_path,
        max_journal_entries=config.journal_entries,
    )


def _build_stack(config: BotConfig):
    """Construct the standard object graph (store, sandbox, tools, client, ...)."""
    logger = setup_logging(config.log_path)
    store = BotStore(config.state_path, config.decay, default_name=config.bot_name)
    sandbox = _sandbox(config)
    sandbox.logger = logger
    toolset = build_toolset(store, sandbox, logger)
    client = OllamaClient(
        config.ollama_url, config.model,
        timeout=config.request_timeout_s, ping_timeout=config.ping_timeout_s,
        logger=logger,
    )
    needle = make_needle(config, client, logger)
    orchestrator = Orchestrator(config, store, sandbox, toolset, client, needle, logger)
    return store, sandbox, toolset, client, needle, orchestrator, logger


def cmd_run(config: BotConfig, args: argparse.Namespace) -> int:
    store, sandbox, toolset, client, needle, orchestrator, logger = _build_stack(config)
    if not args.offline:
        reachable, detail = client.ping()
        if not reachable:
            print(f"⚠ Ollama unreachable — {detail}", file=sys.stderr)
            print("  fix: run `ollama serve`, check ollama_url in config.json, "
                  "or start with `minibot run --offline`", file=sys.stderr)
            return 3
        if not any(m == config.model or m.startswith(config.model + ":")
                   for m in _safe_list_models(client)):
            print(f"⚠ model '{config.model}' is not pulled — run: ollama pull {config.model}",
                  file=sys.stderr)
    from .tui import TerminalUI  # deferred: keeps non-interactive commands light

    ui = TerminalUI(config, store, sandbox, toolset, client, orchestrator, logger)
    return ui.run()


def _safe_list_models(client: OllamaClient) -> list[str]:
    try:
        return client.list_models()
    except OllamaError:
        return []


def cmd_status(config: BotConfig, as_json: bool) -> int:
    store = BotStore(config.state_path, config.decay, default_name=config.bot_name)
    store.tick()
    status = store.status()
    if as_json:
        print(json.dumps(status, indent=2))
        return 0
    print(f"{status['name']} the bot")
    print(f"  mood:      {status['mood']}")
    print(f"  age:       {status['age_days']} days")
    print(f"  hunger:    {status['hunger']}/100 (0 = full, 100 = starving)")
    print(f"  happiness: {status['happiness']}/100")
    print(f"  energy:    {status['energy']}/100")
    print(f"  health:    {status['health']}/100")
    return 0


def cmd_simple_action(config: BotConfig, action: str) -> int:
    store = BotStore(config.state_path, config.decay, default_name=config.bot_name)
    outcome = {"feed_bot": store.feed, "play_with_bot": store.play, "rest_bot": store.rest}[action]()
    print(outcome["message"])
    return 0


def cmd_ls(config: BotConfig, path: str) -> int:
    sandbox = _sandbox(config)
    outcome = sandbox.list_dir(path)
    for entry in outcome["entries"]:
        # dir names already carry a trailing "/" from list_dir
        suffix = "" if entry["type"] == "dir" else f"  ({entry.get('size', 0)} B)"
        print(f"{entry['name']}{suffix}")
    if outcome.get("note"):
        print(f"({outcome['note']})")
    return 0


def cmd_read(config: BotConfig, path: str) -> int:
    sandbox = _sandbox(config)
    outcome = sandbox.read_file(path)
    sys.stdout.write(outcome["content"])
    if outcome["content"] and not outcome["content"].endswith("\n"):
        sys.stdout.write("\n")
    return 0


def cmd_write(config: BotConfig, path: str) -> int:
    sandbox = _sandbox(config)
    content = sys.stdin.read()
    outcome = sandbox.write_file(path, content)
    print(outcome["message"])
    return 0


def cmd_treat(config: BotConfig) -> int:
    store = BotStore(config.state_path, config.decay, default_name=config.bot_name)
    outcome = store.give_treat()
    print(outcome["message"])
    return 0


def cmd_stats(config: BotConfig, as_json: bool, points: int) -> int:
    store = BotStore(config.state_path, config.decay, default_name=config.bot_name)
    store.tick()
    samples = store.stats_history(points)
    if not samples:  # brand-new bot: capture the current reading so the chart isn't empty
        store.record_sample()
        store.save()
        samples = store.stats_history(points)
    if as_json:
        print(json.dumps({"name": store.state.name, "samples": samples}, indent=2))
        return 0
    _print_stats_chart(store.state.name, samples, store.status())
    return 0


def _print_stats_chart(name: str, samples: list[dict], status: dict) -> None:
    print(f"{name} — last {len(samples)} samples")
    print(f"  treats in the bag: {status.get('treats', 0)}")
    if not samples:
        print("  (no samples recorded yet — the chart fills in as you interact)")
        return
    width = 40
    series = [("hunger", "H"), ("happiness", "+"), ("energy", "E"), ("health", "♥")]
    for key, symbol in series:
        if samples[-1][key] >= 99.5:
            glyph = "█"
        elif samples[-1][key] <= 0.5:
            glyph = "·"
        else:
            glyph = symbol
        blocks = " ".join(_spark(sample[key], width, glyph) for sample in samples[-width:])
        print(f"  {key:<9} {blocks}  {status[key]:>3}")
    first, last = samples[0], samples[-1]
    print(f"  trend: hunger {first['hunger']:.0f}→{last['hunger']:.0f}  "
          f"happiness {first['happiness']:.0f}→{last['happiness']:.0f}  "
          f"energy {first['energy']:.0f}→{last['energy']:.0f}  "
          f"health {first['health']:.0f}→{last['health']:.0f}")


def _spark(value: float, width: int, glyph: str) -> str:
    height = 6
    level = max(0, min(height, int(round(float(value) / 100.0 * height))))
    return glyph * level + "·" * (height - level)


def cmd_history(config: BotConfig, limit: int) -> int:
    sandbox = _sandbox(config)
    outcome = sandbox.history(limit)
    if not outcome["entries"]:
        print("no recorded writes yet")
        return 0
    for entry in outcome["entries"]:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.get("t", 0)))
        undoable = "" if entry.get("undoable", True) else "  (not undoable)"
        print(f"{stamp}  {entry.get('action', 'write'):<9} {entry.get('path')} "
              f"({entry.get('bytes', 0)} B){undoable}")
    print(f"({outcome['count']} of {outcome['total']} recorded writes)")
    return 0


def cmd_undo(config: BotConfig, path: str | None) -> int:
    sandbox = _sandbox(config)
    outcome = sandbox.undo(path or None)
    print(outcome["message"])
    return 0


def cmd_mcp(config: BotConfig) -> int:
    from .mcp_server import MCPServer

    _, _, toolset, _, _, _, logger = _build_stack(config)
    server = MCPServer(toolset, logger=logger)
    return server.serve(sys.stdin, sys.stdout)


def cmd_chat(config: BotConfig, message: str) -> int:
    _, sandbox, toolset, client, needle, orchestrator, _ = _build_stack(config)
    turn = orchestrator.handle(message)
    print(turn.text)
    if turn.tools_run:
        print(f"[tools: {', '.join(turn.tools_run)}]", file=sys.stderr)
    return 0


def cmd_doctor(config: BotConfig) -> int:
    store = BotStore(config.state_path, config.decay, default_name=config.bot_name)
    sandbox = _sandbox(config)
    client = OllamaClient(
        config.ollama_url, config.model,
        timeout=config.request_timeout_s, ping_timeout=config.ping_timeout_s,
    )
    checks = collect_checks(config, client, sandbox, store)
    unicode_ok = "utf" in (getattr(sys.stdout, "encoding", "") or "").lower()
    all_ok = print_checks(checks, emit=print, unicode_ok=unicode_ok)
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config, _overrides(args))
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for warning in config.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    command = args.command or "run"
    try:
        if command == "run":
            return cmd_run(config, args)
        if command == "status":
            return cmd_status(config, args.json)
        if command in ("feed", "play", "rest"):
            return cmd_simple_action(config, {"feed": "feed_bot", "play": "play_with_bot",
                                              "rest": "rest_bot"}[command])
        if command == "ls":
            return cmd_ls(config, args.path)
        if command == "read":
            return cmd_read(config, args.path)
        if command == "write":
            return cmd_write(config, args.path)
        if command == "chat":
            return cmd_chat(config, " ".join(args.message))
        if command == "doctor":
            return cmd_doctor(config)
        if command == "treat":
            return cmd_treat(config)
        if command == "stats":
            return cmd_stats(config, args.json, args.points)
        if command == "history":
            return cmd_history(config, args.limit)
        if command == "undo":
            return cmd_undo(config, args.path)
        if command == "mcp":
            return cmd_mcp(config)
    except (SandboxError, BotError, ToolError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except OllamaUnreachable as e:
        print(f"⚠ Ollama unreachable — {e}", file=sys.stderr)
        return 3
    except OllamaError as e:
        print(f"⚠ Ollama error — {e}", file=sys.stderr)
        return 3
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
