"""Simulated GPS: a vehicle driving along a route polyline.

Only used when ``gps.mode: simulated`` or ``--demo`` is requested explicitly.
The route is driven out and back repeatedly, so every hazard found on the
outbound leg is met again on the return leg.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

from mapping.geo import bearing_deg, haversine_m, destination_point
from .gps_service import GPSProvider, GPSState

log = logging.getLogger("smartroad.gps.sim")

DEFAULT_ROUTE: List[Tuple[float, float]] = [(28.6139, 77.2090), (28.6160, 77.2110)]


def load_route(path: Path) -> List[Tuple[float, float]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        pts = [(float(p["latitude"]), float(p["longitude"])) for p in data]
        if len(pts) >= 2:
            return pts
    except Exception as exc:  # noqa: BLE001
        log.error("Cannot load demo route %s: %s (using built-in route)", path, exc)
    return list(DEFAULT_ROUTE)


class GPSSimulator(GPSProvider):
    def __init__(self, route: List[Tuple[float, float]], speed_kmh: float = 45.0,
                 time_scale: float = 1.0, noise_m: float = 0.0, ping_pong: bool = True):
        self.route = route
        self.speed_ms = speed_kmh / 3.6
        self.time_scale = time_scale
        self.noise_m = noise_m
        full = list(route)
        if ping_pong:
            full += list(reversed(route))[1:]
        self.path = full
        self.cum = [0.0]
        for a, b in zip(full, full[1:]):
            self.cum.append(self.cum[-1] + haversine_m(a[0], a[1], b[0], b[1]))
        self.total = self.cum[-1]
        self.leg_len = self.total / 2 if ping_pong else self.total
        self._dist = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state = GPSState(source="simulated", status="NO_FIX", message="starting")
        self._rng = random.Random(7)
        self.laps = 0

    # ----------------------------------------------------------------- core
    def _locate(self, dist: float) -> Tuple[float, float, float]:
        dist = dist % self.total
        lo, hi = 0, len(self.cum) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= dist:
                lo = mid
            else:
                hi = mid
        a, b = self.path[lo], self.path[lo + 1]
        seg = max(1e-9, self.cum[lo + 1] - self.cum[lo])
        f = (dist - self.cum[lo]) / seg
        lat, lon = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        return lat, lon, bearing_deg(a[0], a[1], b[0], b[1])

    def true_state(self) -> Tuple[float, float, float]:
        """Noise-free position/heading (used by the synthetic camera)."""
        with self._lock:
            return self._locate(self._dist)

    def step(self, dt: float) -> GPSState:
        with self._lock:
            before = self._dist
            self._dist += self.speed_ms * dt * self.time_scale
            if int(self._dist // self.total) > int(before // self.total):
                self.laps += 1
            lat, lon, hdg = self._locate(self._dist)
            if self.noise_m:
                lat, lon = destination_point(lat, lon, self._rng.uniform(0, 360),
                                             abs(self._rng.gauss(0, self.noise_m)))
            self._state = GPSState(latitude=lat, longitude=lon, altitude=215.0,
                                   speed=self.speed_ms * self.time_scale, heading=hdg, accuracy=1.5,
                                   satellites=12, fix=True, timestamp=time.time(),
                                   source="simulated", status="OK", message="SIMULATED")
            return self._state

    def get_state(self) -> GPSState:
        with self._lock:
            return GPSState(**self._state.__dict__)

    # ------------------------------------------------------------- threading
    def start(self) -> None:
        self.step(0.0)
        self._thread = threading.Thread(target=self._run, name="gps-sim", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        last = time.time()
        while not self._stop.wait(0.1):
            now = time.time()
            self.step(now - last)
            last = now
