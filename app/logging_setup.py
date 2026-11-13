"""Rotating-file + console logging (logs/smartroad.log)."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):
        if getattr(h, "_smartroad", False):
            root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(log_dir / "smartroad.log", maxBytes=2_000_000,
                                              backupCount=5, encoding="utf-8")
    ch = logging.StreamHandler()
    for h in (fh, ch):
        h.setFormatter(fmt)
        h._smartroad = True  # type: ignore[attr-defined]
        root.addHandler(h)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
