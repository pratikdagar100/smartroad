"""Configuration loading for SmartRoad.

Defaults live here, ``config.yaml`` overrides them, and environment variables
of the form ``SMARTROAD__SECTION__KEY=value`` override both.  Nothing
hardware-specific (ports, camera index, model path, DB path) is hardcoded
outside of this default table.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: Dict[str, Any] = {
    "app": {
        "name": "SmartRoad",
        "host": "127.0.0.1",
        "port": 8000,
        "allow_lan": False,          # True -> bind 0.0.0.0 (explicit opt-in)
        "log_dir": "logs",
        "log_level": "INFO",
        "vehicle_name": "SmartRoad Vehicle",
    },
    "demo": {
        "enabled": False,
        "route_file": "data/demo_route.json",
        "hazards_file": "data/demo_hazards.json",
        "speed_kmh": 45.0,
        "time_scale": 1.0,
        "reset_db": True,
        "db_path": "data/smartroad_demo.db",
        "preseed_known": True,       # a few hazards already "known" at start
        "gps_noise_m": 0.4,
    },
    "camera": {
        "backend": "auto",           # auto | opencv | picamera2 | video | image | demo
        "device": 0,                 # webcam index or /dev/videoN
        "source": "",                # file path for video / image backends
        "width": 1280,
        "height": 720,
        "fps": 30,
        "process_width": 640,        # frames are downscaled to this before AI
        "stream_fps": 15,
        "jpeg_quality": 70,
        "flip": False,
        "reconnect_seconds": 3.0,
    },
    "model": {
        "path": "models/pothole.onnx",
        "backend": "auto",           # auto | onnx | tflite
        "confidence": 0.45,
        "iou": 0.50,
        "input_size": 640,
        "classes": ["pothole"],
        "output_format": "auto",     # auto | yolov8 | yolov5 | nms
        "threads": 4,
        "frame_skip": 1,             # run inference on every Nth frame
    },
    "events": {
        "min_confidence": 0.55,
        "min_stable_frames": 5,
        "min_distance_between_events": 3.0,
        "cooldown_seconds": 5.0,
        "max_track_misses": 5,
        "track_iou": 0.15,
        "min_speed_kmh": 0.0,        # ignore detections when slower than this
    },
    "geolocation": {
        "mode": "fixed",             # fixed | bbox
        "distance_estimate_m": 8.0,  # POTHOLE_DISTANCE_ESTIMATE
        "horizon_ratio": 0.42,
        "ground_k": 2.5,
        "min_distance_m": 3.0,
        "max_distance_m": 40.0,
        "hfov_deg": 70.0,
    },
    "gps": {
        "mode": "serial",            # serial | simulated
        "device": "/dev/ttyUSB0",
        "baudrate": 9600,
        "max_age_s": 3.0,            # older than this -> signal lost
        "min_satellites": 4,
        "reconnect_seconds": 3.0,
    },
    "hazards": {
        "merge_radius_m": 4.0,       # HAZARD_MERGE_RADIUS
        "image_dir": "data/hazards",
    },
    "proximity": {
        "warning_distance_m": 50.0,
        "critical_distance_m": 20.0,
        "hysteresis_factor": 1.3,
        "ahead_angle_deg": 75.0,
        "lookup_radius_m": 300.0,
        "new_hazard_quiet_s": 60.0,  # no "known" warning for a hazard just found
        "min_heading_speed_ms": 1.0,
    },
    "route": {
        "track_interval_s": 1.0,     # GPS_TRACK_INTERVAL
        "min_distance_m": 3.0,
        "gap_new_route_s": 300.0,
        "repeat_tolerance_m": 25.0,
        "repeat_min_overlap": 0.6,
    },
    "severity": {
        "w_area": 0.35,
        "w_confidence": 0.25,
        "w_repeat": 0.15,
        "w_proximity": 0.10,
        "w_impact": 0.15,
        "area_full_scale": 0.06,     # bbox/frame ratio that scores 1.0
        "medium_threshold": 0.35,    # severity threshold
        "high_threshold": 0.65,
    },
    "imu": {
        "enabled": False,
        "bus": 1,
        "address": 0x68,
        "impact_g": 1.6,
        "window_s": 3.0,
        "verify_radius_m": 25.0,
    },
    "privacy": {
        "enabled": False,
        "blur_faces": True,
        "blur_plates": True,
    },
    "map": {
        "tile_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "offline_tiles_dir": "data/tiles",   # optional {z}/{x}/{y}.png pyramid
        "default_zoom": 17,
        "zoom_by_speed": True,
        "default_center": [28.6139, 77.2090],
    },
    "database": {
        "path": "data/smartroad.db",
    },
    "display": {
        "brightness": None,          # 0-100, only applied if backlight exists
    },
    "cloud": {
        "enabled": False,            # optional; local SQLite stays source of truth
        "url_env": "SMARTROAD_CLOUD_URL",
        "token_env": "SMARTROAD_CLOUD_TOKEN",
    },
}


class ConfigError(ValueError):
    pass


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Section:
    """Attribute-style, read/write view over a nested dict."""

    def __init__(self, data: Dict[str, Any]):
        object.__setattr__(self, "_d", data)

    def __getattr__(self, name: str) -> Any:
        try:
            v = self._d[name]
        except KeyError:
            raise AttributeError(name) from None
        return Section(v) if isinstance(v, dict) else v

    def __setattr__(self, name: str, value: Any) -> None:
        self._d[name] = value

    def get(self, name: str, default: Any = None) -> Any:
        v = self._d.get(name, default)
        return Section(v) if isinstance(v, dict) else v

    def to_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(self._d)


class Config(Section):
    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None):
        super().__init__(data)
        object.__setattr__(self, "path", path)

    @property
    def root(self) -> Path:
        return PROJECT_ROOT

    def resolve(self, p: str | os.PathLike) -> Path:
        """Resolve a config path relative to the project root."""
        pp = Path(p)
        return pp if pp.is_absolute() else PROJECT_ROOT / pp

    @property
    def demo_enabled(self) -> bool:
        return bool(self._d["demo"]["enabled"])

    @property
    def db_path(self) -> Path:
        if self.demo_enabled:
            return self.resolve(self._d["demo"]["db_path"])
        return self.resolve(self._d["database"]["path"])

    @property
    def bind_host(self) -> str:
        a = self._d["app"]
        return "0.0.0.0" if a["allow_lan"] else a["host"]


def _coerce(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _apply_env(data: Dict[str, Any]) -> None:
    for key, val in os.environ.items():
        if not key.startswith("SMARTROAD__"):
            continue
        parts = [p.lower() for p in key.split("__")[1:]]
        if len(parts) < 2:
            continue
        node = data
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = _coerce(val)


def validate(d: Dict[str, Any]) -> None:
    def need(cond: bool, msg: str) -> None:
        if not cond:
            raise ConfigError(msg)

    m, e, p = d["model"], d["events"], d["proximity"]
    need(0.0 < m["confidence"] <= 1.0, "model.confidence must be in (0,1]")
    need(0.0 < m["iou"] <= 1.0, "model.iou must be in (0,1]")
    need(len(m["classes"]) >= 1, "model.classes must not be empty")
    need(m["frame_skip"] >= 1, "model.frame_skip must be >= 1")
    need(e["min_stable_frames"] >= 1, "events.min_stable_frames must be >= 1")
    need(0.0 <= e["min_confidence"] <= 1.0, "events.min_confidence must be in [0,1]")
    need(p["critical_distance_m"] <= p["warning_distance_m"],
         "proximity.critical_distance_m must be <= warning_distance_m")
    need(d["hazards"]["merge_radius_m"] > 0, "hazards.merge_radius_m must be > 0")
    need(d["gps"]["mode"] in ("serial", "simulated"), "gps.mode must be serial|simulated")
    need(d["geolocation"]["mode"] in ("fixed", "bbox"), "geolocation.mode must be fixed|bbox")


def load_config(path: str | os.PathLike | None = None,
                overrides: Optional[Dict[str, Any]] = None) -> Config:
    """Load defaults + YAML + env + explicit overrides."""
    cfg_path = Path(path or os.environ.get("SMARTROAD_CONFIG", PROJECT_ROOT / "config.yaml"))
    data = copy.deepcopy(DEFAULTS)
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {cfg_path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"{cfg_path} must contain a mapping at top level")
        data = _deep_merge(data, loaded)
    _apply_env(data)
    if overrides:
        data = _deep_merge(data, overrides)
    validate(data)
    return Config(data, cfg_path if cfg_path.exists() else None)
