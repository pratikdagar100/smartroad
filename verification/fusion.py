"""Verification engine: potential pothole + GPS (+ IMU) -> confirmed event.

    Camera detection -> tracker (stable?) -> gate on GPS/cooldown/distance
                                          -> geolocate -> severity -> CandidateEvent
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from gps.gps_service import GPSState
from mapping.geo import destination_point, haversine_m, lat_lon_valid
from mapping.geolocate import DistanceEstimator, locate_hazard
from .severity import estimate_severity

log = logging.getLogger("smartroad.fusion")


@dataclass
class CandidateEvent:
    hazard_type: str
    confidence: float
    bbox_norm: Tuple[float, float, float, float]
    bbox_px: Tuple[int, int, int, int]
    latitude: float
    longitude: float
    distance_m: float
    vehicle_lat: float
    vehicle_lon: float
    speed: float
    heading: Optional[float]
    severity: str
    severity_score: float
    impact_g: Optional[float] = None
    verified: bool = False
    timestamp: float = field(default_factory=time.time)


class EventEngine:
    def __init__(self, cfg, estimator: DistanceEstimator, imu=None):
        self.cfg = cfg
        self.estimator = estimator
        self.imu = imu
        self._recent: Deque[Tuple[float, str, float, float]] = deque()  # (t, type, lat, lon)
        self.rejected: Dict[str, int] = {}

    def _reject(self, why: str) -> None:
        self.rejected[why] = self.rejected.get(why, 0) + 1

    def _is_recent_duplicate(self, now: float, htype: str, lat: float, lon: float) -> bool:
        ev = self.cfg.events
        while self._recent and now - self._recent[0][0] > ev.cooldown_seconds:
            self._recent.popleft()
        return any(t == htype and haversine_m(lat, lon, la, lo) < ev.min_distance_between_events
                   for _, t, la, lo in self._recent)

    def process(self, track, gps: GPSState, frame_wh: Tuple[int, int],
                now: Optional[float] = None, frame_ts: Optional[float] = None) -> Optional[CandidateEvent]:
        """Evaluate one *confirmed* track; return an event or None.

        ``frame_ts`` (capture time) lets us dead-reckon the vehicle from the
        timestamp of its last GPS fix to the moment the frame was taken.
        """
        now = time.time() if now is None else now
        ev = self.cfg.events
        if track.confidence < ev.min_confidence:
            self._reject("low_confidence")
            return None
        if not (gps.fix and lat_lon_valid(gps.latitude, gps.longitude)):
            self._reject("no_gps")
            return None
        if gps.speed * 3.6 < ev.min_speed_kmh:
            self._reject("too_slow")
            return None
        w, h = frame_wh
        x1, y1, x2, y2 = track.bbox
        bn = (x1 / w, y1 / h, x2 / w, y2 / h)
        vlat, vlon = gps.latitude, gps.longitude
        if frame_ts is not None and gps.heading is not None and gps.speed > 0.5:
            dt = max(-0.5, min(2.0, frame_ts - gps.timestamp))
            vlat, vlon = destination_point(vlat, vlon, gps.heading, gps.speed * dt)
        geo = locate_hazard(vlat, vlon, gps.heading, bn, self.estimator)
        if self._is_recent_duplicate(now, track.cls_name, geo.latitude, geo.longitude):
            self._reject("recent_duplicate")
            return None

        impact = None
        verified = False
        if self.imu is not None:
            impact = self.imu.recent_peak(self.cfg.imu.window_s)
            verified = impact is not None and impact >= self.cfg.imu.impact_g
        score, label, _ = estimate_severity(
            self.cfg.severity, bn, track.confidence,
            distance_m=geo.distance_m or None, detection_count=track.hits,
            impact_g=impact, impact_threshold_g=self.cfg.imu.impact_g)
        self._recent.append((now, track.cls_name, geo.latitude, geo.longitude))
        return CandidateEvent(
            hazard_type=track.cls_name, confidence=round(track.confidence, 3), bbox_norm=bn,
            bbox_px=(int(x1), int(y1), int(x2), int(y2)), latitude=geo.latitude, longitude=geo.longitude,
            distance_m=geo.distance_m, vehicle_lat=vlat, vehicle_lon=vlon,
            speed=gps.speed, heading=gps.heading, severity=label, severity_score=score,
            impact_g=impact, verified=verified, timestamp=now)
