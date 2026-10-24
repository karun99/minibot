"""Sandboxed filesystem access (FR-4).

Every path is resolved (symlinks followed) and must stay inside the sandbox
root. Traversal, absolute escapes, null bytes and oversized files are refused.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("minibot.sandbox")


class SandboxError(Exception):
    """A file operation was refused by the sandbox."""


@dataclass
class Sandbox:
    root: Path
    max_file_bytes: int = 1_048_576
    max_list_entries: int = 500
    logger: logging.Logger | None = None
    journal_path: Path | str | None = None      # enables /history + /undo
    max_journal_entries: int = 100

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.root = self.root.resolve()
        except OSError as e:
            raise SandboxError(f"cannot create sandbox at {self.root}: {e}") from e
        if self.journal_path:
            self.journal_path = Path(self.journal_path).expanduser()

    # -- path safety ---------------------------------------------------------

    def resolve(self, path: str) -> Path:
        """Resolve `path` inside the sandbox or raise SandboxError (FR-4.1/4.2)."""
        if path is None:
            raise SandboxError("path is required")
        raw = str(path)
        if not raw.strip():
            raise SandboxError("path must not be empty")
        if "\x00" in raw:
            raise SandboxError("path contains an illegal null byte")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve()  # follows symlinks; strict=False by default
        except (OSError, RuntimeError) as e:  # RuntimeError: symlink loops
            raise SandboxError(f"cannot resolve path: {e}") from e
        if resolved != self.root and self.root not in resolved.parents:
            raise SandboxError(f"path is outside the sandbox: {path}")
        return resolved

    def relpath(self, target: Path) -> str:
        try:
            rel = target.relative_to(self.root)
        except ValueError:
            return target.as_posix()
        return rel.as_posix() or "."

    def _log(self, level: str, message: str) -> None:
        getattr(self.logger or log, level)(message)

    # -- operations ----------------------------------------------------------

    def read_file(self, path: str) -> dict:
        target = self.resolve(path)
        if not target.exists():
            raise SandboxError(f"no such file: {self.relpath(target)}")
        if target.is_dir():
            raise SandboxError(f"is a directory: {self.relpath(target)} (use list_dir)")
        if not target.is_file():
            raise SandboxError(f"not a regular file: {self.relpath(target)}")
        try:
            size = target.stat().st_size
        except OSError as e:
            raise SandboxError(f"cannot stat {self.relpath(target)}: {e}") from e
        if size > self.max_file_bytes:
            raise SandboxError(
                f"file too large: {size} bytes exceeds limit of {self.max_file_bytes}"
            )
        try:
            data = target.read_bytes()
        except OSError as e:
            raise SandboxError(f"cannot read {self.relpath(target)}: {e}") from e
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise SandboxError(f"not a UTF-8 text file: {self.relpath(target)}") from e
        self._log("debug", f"read_file path={self.relpath(target)} bytes={size}")
        return {"ok": True, "path": self.relpath(target), "size": size, "content": text}

    def write_file(self, path: str, content: str) -> dict:
        if not isinstance(content, str):
            raise SandboxError("content must be a string")
        try:
            data = content.encode("utf-8")
        except UnicodeEncodeError as e:
            raise SandboxError(f"content is not valid UTF-8 text: {e}") from e
        if len(data) > self.max_file_bytes:
            raise SandboxError(
                f"content too large: {len(data)} bytes exceeds limit of {self.max_file_bytes}"
            )
        target = self.resolve(path)
        if target.exists() and target.is_dir():
            raise SandboxError(f"is a directory: {self.relpath(target)}")
        existed = target.exists() and target.is_file()
        prev_b64 = None
        if existed:
            try:
                if target.stat().st_size <= self.max_file_bytes:
                    prev_b64 = base64.b64encode(target.read_bytes()).decode("ascii")
            except OSError:
                prev_b64 = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as e:
            raise SandboxError(f"cannot write {self.relpath(target)}: {e}") from e
        rel = self.relpath(target)
        self._journal_append(
            {
                "t": time.time(),
                "action": "overwrite" if existed else "create",
                "path": rel,
                "bytes": len(data),
                "prev_b64": prev_b64,
                "undoable": (prev_b64 is not None) or (not existed),
            }
        )
        self._log("info", f"file_write path={rel} bytes={len(data)}")  # FR-4.4 audit
        return {"ok": True, "path": rel, "size": len(data), "message": f"wrote {rel} ({len(data)} bytes)"}

    # -- journal (history + undo) -------------------------------------------

    def _journal_read(self) -> list[dict]:
        if not self.journal_path:
            return []
        path = Path(self.journal_path)
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            self._log("warning", f"journal unreadable: {e}")
            return []
        entries: list[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("path"):
                entries.append(item)
        return entries

    def _journal_write(self, entries: list[dict]) -> None:
        if not self.journal_path:
            return
        path = Path(self.journal_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in entries),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as e:
            self._log("warning", f"journal write failed: {e}")  # never break the write itself

    def _journal_append(self, entry: dict) -> None:
        if not self.journal_path:
            return
        entries = self._journal_read()
        entries.append(entry)
        overflow = len(entries) - max(1, self.max_journal_entries)
        if overflow > 0:
            del entries[:overflow]
        self._journal_write(entries)

    def history(self, limit: int = 20) -> dict:
        """Recent sandbox writes, oldest first (contents stripped)."""
        if not self.journal_path:
            raise SandboxError("journal is disabled — no write history available")
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 20
        entries = self._journal_read()
        recent = []
        for item in entries[-limit:]:
            slim = {k: v for k, v in item.items() if k != "prev_b64"}
            if item.get("prev_b64"):
                try:
                    slim["prev_bytes"] = len(base64.b64decode(item["prev_b64"]))
                except (ValueError, TypeError):
                    slim["prev_bytes"] = -1
            recent.append(slim)
        return {"ok": True, "entries": recent, "count": len(recent), "total": len(entries)}

    def undo(self, path: str | None = None) -> dict:
        """Revert the most recent write (or the most recent write to `path`)."""
        if not self.journal_path:
            raise SandboxError("journal is disabled — undo is unavailable")
        entries = self._journal_read()
        if not entries:
            raise SandboxError("nothing to undo")
        if path:
            wanted = self.relpath(self.resolve(path))
            index = next(
                (i for i in range(len(entries) - 1, -1, -1) if entries[i].get("path") == wanted),
                None,
            )
            if index is None:
                raise SandboxError(f"no journal entry for {wanted}")
        else:
            index = len(entries) - 1
        entry = entries[index]
        if not entry.get("undoable", True):
            raise SandboxError(
                f"cannot undo {entry['path']}: the previous version was not captured "
                f"(file exceeded the {self.max_file_bytes} byte snapshot limit)"
            )
        target = self.resolve(entry["path"])
        restored = entry.get("prev_b64")
        if restored is not None:
            try:
                data = base64.b64decode(restored)
            except (ValueError, TypeError) as e:
                raise SandboxError(f"journal entry for {entry['path']} is corrupt: {e}") from e
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            except OSError as e:
                raise SandboxError(f"cannot restore {entry['path']}: {e}") from e
            outcome = {
                "ok": True,
                "path": entry["path"],
                "restored_bytes": len(data),
                "message": f"restored {entry['path']} ({len(data)} bytes)",
            }
        else:
            try:
                if target.exists():
                    target.unlink()
            except OSError as e:
                raise SandboxError(f"cannot remove {entry['path']}: {e}") from e
            outcome = {
                "ok": True,
                "path": entry["path"],
                "deleted": True,
                "message": f"removed {entry['path']} (it did not exist before this write)",
            }
        del entries[index]
        self._journal_write(entries)
        self._log("info", f"undo path={entry['path']}")
        return outcome

    def list_dir(self, path: str = ".") -> dict:
        target = self.resolve(path)
        if not target.exists():
            raise SandboxError(f"no such directory: {self.relpath(target)}")
        if not target.is_dir():
            raise SandboxError(f"not a directory: {self.relpath(target)}")
        try:
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as e:
            raise SandboxError(f"cannot list {self.relpath(target)}: {e}") from e
        out: list[dict] = []
        truncated = False
        for i, entry in enumerate(entries):
            if i >= self.max_list_entries:
                truncated = True
                break
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            item = {"name": entry.name + ("/" if is_dir else ""), "type": "dir" if is_dir else "file"}
            if not is_dir:
                try:
                    item["size"] = entry.stat().st_size
                except OSError:
                    item["size"] = -1
            out.append(item)
        result: dict = {"ok": True, "path": self.relpath(target), "entries": out, "count": len(out)}
        if truncated:
            result["note"] = f"truncated at {self.max_list_entries} entries ({len(entries)} total)"
        self._log("debug", f"list_dir path={result['path']} count={len(out)}")
        return result
