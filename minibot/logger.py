"""Central logging (NFR-5): rotating local log file, never stdout (the TUI owns stdout)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(log_path: str | Path, level: str = "INFO") -> logging.Logger:
    """Configure the 'minibot' logger tree to write to a rotating file."""
    logger = logging.getLogger("minibot")
    logger.setLevel(level.upper())
    logger.propagate = False
    if not logger.handlers:
        path = Path(log_path).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = RotatingFileHandler(
                path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
        except OSError:
            # Read-only home etc: keep running without logs rather than crashing.
            handler = logging.NullHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
    return logger
