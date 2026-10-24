"""Sandboxed filesystem access (FR-4).

Every path is resolved (symlinks followed) and must stay inside the sandbox
root. Traversal, absolute escapes, null bytes and oversized files are refused.
"""

from __future__ import annotations

import logging
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

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.root = self.root.resolve()
        except OSError as e:
            raise SandboxError(f"cannot create sandbox at {self.root}: {e}") from e

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
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as e:
            raise SandboxError(f"cannot write {self.relpath(target)}: {e}") from e
        rel = self.relpath(target)
        self._log("info", f"file_write path={rel} bytes={len(data)}")  # FR-4.4 audit
        return {"ok": True, "path": rel, "size": len(data), "message": f"wrote {rel} ({len(data)} bytes)"}

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
