"""Estimate where a detected hazard actually is on the ground.

The hazard is *ahead* of the vehicle, not under the GPS antenna.  We combine
vehicle GPS + heading + an estimated distance (+ a small lateral bearing
offset from the pixel column).

``DistanceEstimator`` is the extension point: a LiDAR / stereo / depth sensor
can subclass it and return a measured range instead of an estimate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

from .geo import destination_point


@dataclass
class GeoEstimate:
    latitude: float
    longitude: float
    distance_m: float
    bearing: float
    method: str  # "fixed" | "bbox" | "sensor"


class DistanceEstimator:
    """Base class - override ``estimate`` to plug in a real distance sensor."""

    name = "base"

    def estimate(self, bbox_norm: Tuple[float, float, float, float]) -> float:
        raise NotImplementedError

    def lateral_offset_deg(self, bbox_norm: Tuple[float, float, float, float]) -> float:
        return 0.0


class FixedDistanceEstimator(DistanceEstimator):
    """POTHOLE_DISTANCE_ESTIMATE: a constant forward offset."""

    name = "fixed"

    def __init__(self, distance_m: float):
        self.distance_m = float(distance_m)

    def estimate(self, bbox_norm):
        return self.distance_m


class FlatRoadEstimator(DistanceEstimator):
    """Pinhole / flat-road model: range from the bottom edge of the bbox.

    ``distance = ground_k / (y_bottom - horizon)`` with y in [0,1].  It is an
    *estimate* that needs per-camera calibration (horizon_ratio, ground_k).
    """

    name = "bbox"

    def __init__(self, horizon_ratio: float, ground_k: float, min_m: float,
                 max_m: float, hfov_deg: float):
        self.horizon = horizon_ratio
        self.k = ground_k
        self.min_m = min_m
        self.max_m = max_m
        self.hfov = hfov_deg

    def estimate(self, bbox_norm):
        yb = bbox_norm[3]
        dy = max(1e-3, yb - self.horizon)
        return min(self.max_m, max(self.min_m, self.k / dy))

    def lateral_offset_deg(self, bbox_norm):
        cx = (bbox_norm[0] + bbox_norm[2]) / 2.0
        t = (cx - 0.5) * 2.0 * math.tan(math.radians(self.hfov / 2.0))
        return math.degrees(math.atan(t))


def make_estimator(cfg) -> DistanceEstimator:
    g = cfg.geolocation
    if g.mode == "bbox":
        return FlatRoadEstimator(g.horizon_ratio, g.ground_k, g.min_distance_m,
                                 g.max_distance_m, g.hfov_deg)
    return FixedDistanceEstimator(g.distance_estimate_m)


def locate_hazard(vehicle_lat: float, vehicle_lon: float, heading: Optional[float],
                  bbox_norm: Tuple[float, float, float, float],
                  estimator: DistanceEstimator) -> GeoEstimate:
    """Project the hazard from the vehicle position along the heading."""
    dist = estimator.estimate(bbox_norm)
    if heading is None:
        # Without a heading we cannot project; fall back to the vehicle position.
        return GeoEstimate(vehicle_lat, vehicle_lon, 0.0, 0.0, "no-heading")
    brg = (heading + estimator.lateral_offset_deg(bbox_norm)) % 360.0
    lat, lon = destination_point(vehicle_lat, vehicle_lon, brg, dist)
    return GeoEstimate(lat, lon, dist, brg, estimator.name)
