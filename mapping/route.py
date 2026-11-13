"""Route recording and route-repeat matching."""
from __future__ import annotations

import logging
import time
from typing import List, Optional, Sequence, Tuple

from .geo import haversine_m, lat_lon_valid, min_distance_to_points, resample_path

log = logging.getLogger("smartroad.route")


class RouteRecorder:
    """Stores GPS points when enough time AND distance have passed."""

    def __init__(self, db, vehicle_id: int, interval_s: float = 1.0, min_distance_m: float = 3.0,
                 gap_new_route_s: float = 300.0):
        self.db, self.vehicle_id = db, vehicle_id
        self.interval_s, self.min_distance_m, self.gap_s = interval_s, min_distance_m, gap_new_route_s
        self.route_id: Optional[int] = None
        self.points: List[Tuple[float, float]] = []
        self.distance_m = 0.0
        self._last_t = 0.0
        self._last_fix_t: Optional[float] = None

    def _begin(self) -> None:
        self.route_id = self.db.start_route(self.vehicle_id)
        self.points, self.distance_m = [], 0.0
        log.info("Started route #%s", self.route_id)

    def update(self, lat, lon, altitude=None, speed=None, heading=None, accuracy=None,
               now: Optional[float] = None) -> bool:
        """Feed a GPS fix. Returns True when a point was stored."""
        if not lat_lon_valid(lat, lon):
            return False
        now = time.time() if now is None else now
        if self.route_id is not None and self._last_fix_t is not None and now - self._last_fix_t > self.gap_s:
            self.finish()
        self._last_fix_t = now
        if self.route_id is None:
            self._begin()
        if self.points:
            if now - self._last_t < self.interval_s:
                return False
            d = haversine_m(self.points[-1][0], self.points[-1][1], lat, lon)
            if d < self.min_distance_m:
                return False
            self.distance_m += d
        self.points.append((lat, lon))
        self._last_t = now
        self.db.enqueue_track_point(self.route_id, lat, lon, altitude, speed, heading, accuracy)
        return True

    def finish(self) -> None:
        if self.route_id is not None:
            self.db.flush()
            self.db.end_route(self.route_id, self.distance_m, len(self.points))
            log.info("Finished route #%s: %.0f m, %d points", self.route_id, self.distance_m, len(self.points))
        self.route_id = None
        self.points = []


def route_overlap(current: Sequence[Tuple[float, float]], reference: Sequence[Tuple[float, float]],
                  tolerance_m: float = 25.0) -> float:
    """Fraction (0-1) of ``current`` points that lie within tolerance of ``reference``."""
    if not current or not reference:
        return 0.0
    ref = resample_path(list(reference), max(5.0, tolerance_m / 2))
    near = sum(1 for p in current if min_distance_to_points(p[0], p[1], ref) <= tolerance_m)
    return near / len(current)


def find_repeated_route(db, current: Sequence[Tuple[float, float]], exclude_route_id: Optional[int],
                        tolerance_m: float = 25.0, min_overlap: float = 0.6,
                        max_routes: int = 10) -> Optional[dict]:
    """Return the previous route best matching the current track, if any."""
    if len(current) < 5:
        return None
    sample = list(current)[::max(1, len(current) // 40)]
    best = None
    for r in db.list_routes(max_routes + 1):
        if r["id"] == exclude_route_id or r["point_count"] < 5:
            continue
        pts = db.get_route_points(r["id"], stride=3)
        ov = route_overlap(sample, pts, tolerance_m)
        if ov >= min_overlap and (best is None or ov > best["overlap"]):
            best = {"route_id": r["id"], "name": r["name"], "overlap": round(ov, 2)}
    return best
