"""Known-hazard proximity engine (works fully offline).

Each GPS update the engine measures the distance to stored hazards and
produces:
  * ``nearby``  - hazards within ``lookup_radius_m`` (for the map / API),
  * ``warning`` - the closest hazard that is *ahead* and inside the warning
    distance, with level WARNING or CRITICAL.

A warning is raised once per hazard and stays active (hysteresis) until the
vehicle leaves ``warning_distance * hysteresis_factor`` or passes the hazard,
so it never re-triggers on every GPS tick.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .geo import angle_diff_deg, bearing_deg, haversine_m, lat_lon_valid

LEVEL_NONE, LEVEL_WARNING, LEVEL_CRITICAL = "NONE", "WARNING", "CRITICAL"


@dataclass
class HazardRef:
    id: int
    latitude: float
    longitude: float
    hazard_type: str = "pothole"
    severity: str = "LOW"
    status: str = "ACTIVE"


@dataclass
class ProximityResult:
    nearby: List[dict] = field(default_factory=list)
    warning: Optional[dict] = None
    new_triggers: List[dict] = field(default_factory=list)  # warnings raised this update


class ProximityEngine:
    def __init__(self, warning_m: float = 50.0, critical_m: float = 20.0, hysteresis: float = 1.3,
                 ahead_deg: float = 75.0, lookup_m: float = 300.0, quiet_s: float = 60.0,
                 min_heading_speed: float = 1.0):
        self.warning_m, self.critical_m = warning_m, critical_m
        self.hysteresis, self.ahead_deg = hysteresis, ahead_deg
        self.lookup_m, self.quiet_s = lookup_m, quiet_s
        self.min_heading_speed = min_heading_speed
        self._hazards: Dict[int, HazardRef] = {}
        self._active: Dict[int, str] = {}       # hazard id -> level currently shown
        self._quiet_until: Dict[int, float] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------- hazard set
    def load(self, hazards) -> None:
        with self._lock:
            self._hazards = {h.id: HazardRef(h.id, h.latitude, h.longitude, h.hazard_type, h.severity, h.status)
                             for h in hazards}

    def upsert(self, h) -> None:
        with self._lock:
            self._hazards[h.id] = HazardRef(h.id, h.latitude, h.longitude, h.hazard_type, h.severity, h.status)

    def quiet(self, hazard_id: int, seconds: Optional[float] = None) -> None:
        """Suppress *new* warnings for a hazard we just found ourselves."""
        with self._lock:
            self._quiet_until[hazard_id] = time.time() + (self.quiet_s if seconds is None else seconds)
            self._active.pop(hazard_id, None)

    def count(self) -> int:
        return len(self._hazards)

    def configure(self, **kw) -> None:
        for k, v in kw.items():
            if hasattr(self, k) and v is not None:
                setattr(self, k, float(v))

    # --------------------------------------------------------------- update
    def update(self, lat: Optional[float], lon: Optional[float], heading: Optional[float],
               speed_ms: float = 0.0, now: Optional[float] = None) -> ProximityResult:
        res = ProximityResult()
        if not lat_lon_valid(lat, lon):
            with self._lock:
                self._active.clear()
            return res
        now = time.time() if now is None else now
        use_heading = heading is not None and speed_ms >= self.min_heading_speed
        with self._lock:
            hazards = list(self._hazards.values())
            candidates = []
            for h in hazards:
                if h.status == "FIXED":
                    continue
                # cheap reject before haversine (1 deg lat ~ 111 km)
                if abs(h.latitude - lat) > 0.004 or abs(h.longitude - lon) > 0.005:
                    continue
                d = haversine_m(lat, lon, h.latitude, h.longitude)
                if d > self.lookup_m:
                    continue
                brg = bearing_deg(lat, lon, h.latitude, h.longitude)
                diff = angle_diff_deg(brg, heading) if use_heading else 0.0
                ahead = diff <= self.ahead_deg
                level = LEVEL_NONE
                if ahead and d <= self.critical_m:
                    level = LEVEL_CRITICAL
                elif ahead and d <= self.warning_m:
                    level = LEVEL_WARNING
                candidates.append((d, h, brg, ahead, level))
                res.nearby.append({"id": h.id, "type": h.hazard_type, "severity": h.severity,
                                   "lat": h.latitude, "lon": h.longitude, "distance_m": round(d, 1),
                                   "bearing": round(brg, 1), "ahead": ahead})
            res.nearby.sort(key=lambda x: x["distance_m"])

            # hysteresis: drop active warnings once out of range or passed
            for hid in list(self._active):
                c = next((c for c in candidates if c[1].id == hid), None)
                if c is None or c[0] > self.warning_m * self.hysteresis or not c[3]:
                    del self._active[hid]

            for d, h, brg, ahead, level in sorted(candidates, key=lambda c: c[0]):
                if level == LEVEL_NONE:
                    continue
                if self._quiet_until.get(h.id, 0) > now and h.id not in self._active:
                    continue
                prev = self._active.get(h.id)
                if prev is None or (prev == LEVEL_WARNING and level == LEVEL_CRITICAL):
                    res.new_triggers.append({"id": h.id, "level": level, "distance_m": round(d, 1)})
                self._active[h.id] = level if prev != LEVEL_CRITICAL else LEVEL_CRITICAL

            active = [(d, h, lvl) for d, h, _b, _a, _l in candidates
                      for lvl in [self._active.get(h.id)] if lvl]
            if active:
                d, h, lvl = min(active, key=lambda a: a[0])
                res.warning = {"id": h.id, "type": h.hazard_type, "severity": h.severity,
                               "level": lvl, "distance_m": round(d, 1), "lat": h.latitude, "lon": h.longitude,
                               "text": f"KNOWN {h.hazard_type.upper().replace('_', ' ')} AHEAD"}
        return res
