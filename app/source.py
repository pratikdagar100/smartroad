"""Runtime-selectable input source (live camera / test / video / demo) and model choice.

The choice is saved to ``data/ui_source.json``; the app restarts itself in-process to apply it.
Explicit command-line source flags win on a cold start; the UI choice wins after a UI switch.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

MODES = {
    "live": "LIVE - camera + real GPS",
    "test": "TEST - camera + simulated GPS",
    "video": "VIDEO - file + simulated GPS",
    "demo": "DEMO - fully simulated",
}


def source_file(root: Path) -> Path:
    return root / "data" / "ui_source.json"


def load(root: Path) -> Dict[str, Any]:
    try:
        d = json.loads(source_file(root).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(root: Path, data: Dict[str, Any]) -> None:
    f = source_file(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=1), encoding="utf-8")


def validate(data: Dict[str, Any]) -> Optional[str]:
    mode = data.get("mode")
    if mode not in MODES:
        return f"mode must be one of {sorted(MODES)}"
    if mode == "video" and not Path(str(data.get("video_path", ""))).is_file():
        return "video mode needs an existing video/image file path"
    return None


def to_overrides(data: Dict[str, Any]) -> Dict[str, Any]:
    """Translate the saved choice into config overrides."""
    o: Dict[str, Any] = {}
    mode = data.get("mode")
    cam = o.setdefault("camera", {})
    if data.get("camera_device") not in (None, ""):
        dev = str(data["camera_device"])
        cam["device"] = int(dev) if dev.isdigit() else dev
    if data.get("gps_device"):
        o.setdefault("gps", {})["device"] = data["gps_device"]
    if mode in ("demo", "live", "test"):
        cam["backend"] = "auto"
    if mode == "demo":
        o["demo"] = {"enabled": True}
    elif mode == "live":
        o.setdefault("gps", {})["mode"] = "serial"
    elif mode == "test":
        o.setdefault("gps", {})["mode"] = "simulated"
    elif mode == "video":
        o.setdefault("gps", {})["mode"] = "simulated"
        p = str(data.get("video_path", ""))
        cam["backend"] = "image" if p.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")) else "video"
        cam["source"] = p
    if data.get("model_path"):
        o["model"] = {"path": data["model_path"]}
    return o


def current_mode(cfg) -> str:
    if cfg.demo_enabled:
        return "demo"
    if str(cfg.camera.backend) in ("video", "image"):
        return "video"
    return "test" if cfg.gps.mode == "simulated" else "live"
