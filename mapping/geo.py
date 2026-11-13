"""Geospatial helpers (pure functions, no I/O)."""
from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, 0-360 degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination_point(lat: float, lon: float, bearing: float, distance_m: float) -> Tuple[float, float]:
    """Point reached travelling ``distance_m`` from (lat, lon) on ``bearing`` degrees."""
    d = distance_m / EARTH_RADIUS_M
    b = math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def angle_diff_deg(a: float, b: float) -> float:
    """Smallest absolute difference between two compass angles (0-180)."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def lat_lon_valid(lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    if math.isnan(lat) or math.isnan(lon):
        return False
    if abs(lat) > 90 or abs(lon) > 180:
        return False
    return not (lat == 0.0 and lon == 0.0)


def bbox_around(lat: float, lon: float, radius_m: float) -> Tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lon, max_lon) box that contains the radius circle."""
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    dlon = dlat / max(0.01, math.cos(math.radians(lat)))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def path_length_m(points: Sequence[Tuple[float, float]]) -> float:
    return sum(haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(points, points[1:]))


def resample_path(points: Sequence[Tuple[float, float]], step_m: float) -> List[Tuple[float, float]]:
    """Resample a polyline so consecutive points are ~step_m apart."""
    if len(points) < 2:
        return list(points)
    out = [tuple(points[0])]
    carry = 0.0
    for a, b in zip(points, points[1:]):
        seg = haversine_m(a[0], a[1], b[0], b[1])
        if seg == 0:
            continue
        pos = step_m - carry
        while pos <= seg:
            f = pos / seg
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            pos += step_m
        carry = seg - (pos - step_m)
    if out[-1] != tuple(points[-1]):
        out.append(tuple(points[-1]))
    return out


def min_distance_to_points(lat: float, lon: float, pts: Iterable[Tuple[float, float]]) -> float:
    best = float("inf")
    for p in pts:
        d = haversine_m(lat, lon, p[0], p[1])
        if d < best:
            best = d
    return best
