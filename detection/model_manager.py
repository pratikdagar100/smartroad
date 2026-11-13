"""Model catalogue, download, verification and installation (used by the UI and scripts)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional

log = logging.getLogger("smartroad.models")
MODEL_EXTS = (".onnx", ".tflite")


def load_catalog(models_dir: Path) -> List[dict]:
    try:
        return json.loads((models_dir / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def list_installed(models_dir: Path) -> List[dict]:
    out = []
    for p in sorted(models_dir.glob("*")):
        if p.suffix.lower() in MODEL_EXTS and not p.name.endswith(".part"):
            out.append({"file": p.name, "size_mb": round(p.stat().st_size / 1e6, 1)})
    return out


def verify_onnx(path: Path, num_classes: int) -> str:
    """Load the model and run one dummy inference. Returns a short description or raises ValueError."""
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        raise ValueError(f"onnxruntime not available: {exc}") from exc
    try:
        sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        inp = sess.get_inputs()[0]
        size = inp.shape[2] if isinstance(inp.shape[2], int) else 640
        out = sess.run(None, {inp.name: np.zeros((1, 3, size, size), np.float32)})[0]
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"not a usable ONNX detector: {exc}") from exc
    shp = out.shape
    if len(shp) != 3:
        raise ValueError(f"unexpected output shape {shp}")
    c = min(shp[1], shp[2]) if shp[1] != shp[2] else shp[1]
    if c not in (4 + num_classes, 5 + num_classes, 4 + num_classes + 32, 6):
        raise ValueError(f"model has {c} output channels; does not fit {num_classes} class(es) "
                         f"(expected {4 + num_classes}, {5 + num_classes} or {36 + num_classes}). "
                         "Update model.classes in config.yaml.")
    return f"input {size}, output {tuple(shp)}"


def download(url: str, dest: Path, progress: Optional[Callable[[float], None]] = None,
             sha256: Optional[str] = None) -> None:
    if not url.lower().startswith("https://"):
        raise ValueError("only https:// model URLs are allowed")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "SmartRoad-model-installer/1.0"})
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as fh:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress and total:
                    progress(got / total)
        if sha256 and h.hexdigest().lower() != sha256.lower():
            raise ValueError("download corrupted (SHA-256 mismatch)")
        os.replace(part, dest)
    finally:
        if part.exists():
            part.unlink()


def install_entry(entry: dict, models_dir: Path, progress: Optional[Callable[[float], None]] = None,
                  force: bool = False) -> Path:
    """Download + verify a catalogue entry into models/<file>. Returns the final path."""
    dest = models_dir / entry["file"]
    if dest.exists() and not force:
        verify_onnx(dest, len(entry.get("classes", ["pothole"])))
        return dest
    tmp = models_dir / (entry["file"] + ".new")
    download(entry["url"], tmp, progress, entry.get("sha256"))
    try:
        info = verify_onnx(tmp, len(entry.get("classes", ["pothole"])))
    except ValueError:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, dest)
    log.info("Installed model %s (%s)", dest.name, info)
    return dest


class ModelJob:
    """Background install with progress, polled by the API."""

    def __init__(self):
        self.status = "idle"        # idle | running | done | error
        self.progress = 0.0
        self.message = ""
        self._lock = threading.Lock()

    def snapshot(self) -> Dict:
        with self._lock:
            return {"status": self.status, "progress": round(self.progress, 3), "message": self.message}

    def start(self, entry: dict, models_dir: Path, on_done: Callable[[Path], None]) -> bool:
        with self._lock:
            if self.status == "running":
                return False
            self.status, self.progress, self.message = "running", 0.0, f"downloading {entry['name']}"

        def work():
            try:
                def prog(p):
                    with self._lock:
                        self.progress = p
                path = install_entry(entry, models_dir, prog, force=True)
                on_done(path)
                with self._lock:
                    self.status, self.progress, self.message = "done", 1.0, f"installed {path.name}"
            except Exception as exc:  # noqa: BLE001
                log.error("Model install failed: %s", exc)
                with self._lock:
                    self.status, self.message = "error", str(exc)

        threading.Thread(target=work, name="model-install", daemon=True).start()
        return True
