"""Thread-safe shared state between the pipeline threads and the API."""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional


class SharedState:
    def __init__(self, mode: str = "hardware", gps_mode: str = "serial"):
        self._lock = threading.Lock()
        self.mode = mode                    # "hardware" | "demo" | "test"
        self.gps_mode = gps_mode
        self.started = time.time()
        self.gps: Dict[str, Any] = {}
        self.detections: List[dict] = []
        self.detect_fps = 0.0
        self.stream_fps = 0.0
        self.infer_ms = 0.0
        self.proximity: Dict[str, Any] = {"nearby": [], "warning": None}
        self.alert: Optional[dict] = None   # last "pothole detected" banner
        self.alert_until = 0.0
        self.route: Dict[str, Any] = {"id": None, "distance_m": 0.0, "points": 0, "repeat": None}
        self.subsystems: Dict[str, Dict[str, str]] = {}
        self.hazard_rev = 0
        self.known_count = 0
        self.events: Deque[dict] = deque(maxlen=30)
        self.counters: Dict[str, int] = {"events_created": 0, "events_merged": 0, "frames": 0}
        self._jpeg: Optional[bytes] = None
        self._jpeg_seq = 0
        self._jpeg_cond = threading.Condition()

    # -------------------------------------------------------------- generic
    def set(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def set_subsystem(self, name: str, status: str, message: str = "") -> None:
        """status: OK | WARN | ERROR | OFF"""
        with self._lock:
            self.subsystems[name] = {"status": status, "message": message}

    def bump_hazards(self) -> None:
        with self._lock:
            self.hazard_rev += 1

    def count(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + n

    def add_event(self, kind: str, text: str, **extra) -> None:
        with self._lock:
            self.events.appendleft({"t": time.time(), "kind": kind, "text": text, **extra})

    def raise_alert(self, alert: dict, seconds: float = 4.0) -> None:
        with self._lock:
            self.alert, self.alert_until = alert, time.time() + seconds

    # ----------------------------------------------------------- video frame
    def set_jpeg(self, data: bytes) -> None:
        with self._jpeg_cond:
            self._jpeg = data
            self._jpeg_seq += 1
            self._jpeg_cond.notify_all()

    def get_jpeg(self) -> Optional[bytes]:
        return self._jpeg

    def wait_jpeg(self, last_seq: int, timeout: float = 2.0):
        """Block until a newer frame exists. Returns (seq, bytes|None)."""
        with self._jpeg_cond:
            if self._jpeg_seq == last_seq:
                self._jpeg_cond.wait(timeout)
            return self._jpeg_seq, self._jpeg

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            alert = self.alert if now < self.alert_until else None
            return {
                "t": now,
                "mode": self.mode,
                "gps_mode": self.gps_mode,
                "uptime_s": round(now - self.started, 1),
                "gps": dict(self.gps),
                "speed_kmh": self.gps.get("speed_kmh", 0.0),
                "heading": self.gps.get("heading"),
                "fps": {"detect": round(self.detect_fps, 1), "stream": round(self.stream_fps, 1),
                        "infer_ms": round(self.infer_ms, 1)},
                "detections": list(self.detections),
                "alert": alert,
                "proximity": self.proximity,
                "route": dict(self.route),
                "subsystems": {k: dict(v) for k, v in self.subsystems.items()},
                "hazard_rev": self.hazard_rev,
                "known_hazards": self.known_count,
                "events": list(self.events)[:8],
                "counters": dict(self.counters),
            }
