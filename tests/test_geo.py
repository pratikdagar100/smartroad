import pytest

from mapping.geo import (angle_diff_deg, bearing_deg, destination_point, haversine_m, lat_lon_valid,
                         path_length_m, resample_path)
from mapping.geolocate import FixedDistanceEstimator, FlatRoadEstimator, locate_hazard


def test_haversine_spec_example():
    d = haversine_m(28.612900, 77.031200, 28.613150, 77.031350)
    assert 28 < d < 35           # ~31 m


def test_haversine_zero_and_known():
    assert haversine_m(10, 10, 10, 10) == 0
    assert haversine_m(0, 0, 0, 1) == pytest.approx(111_195, rel=1e-3)


def test_destination_roundtrip():
    lat, lon = destination_point(28.6, 77.2, 90, 100)
    assert haversine_m(28.6, 77.2, lat, lon) == pytest.approx(100, abs=0.1)
    assert bearing_deg(28.6, 77.2, lat, lon) == pytest.approx(90, abs=0.1)


def test_angle_diff_wraps():
    assert angle_diff_deg(350, 10) == 20
    assert angle_diff_deg(0, 180) == 180


def test_valid_coordinates():
    assert lat_lon_valid(28.6, 77.2)
    assert not lat_lon_valid(None, 77)
    assert not lat_lon_valid(0, 0)
    assert not lat_lon_valid(91, 0)


def test_resample():
    pts = [(28.6, 77.2), (28.601, 77.2)]
    r = resample_path(pts, 10)
    assert len(r) >= 10 and path_length_m(r) == pytest.approx(path_length_m(pts), rel=0.01)


def test_fixed_forward_offset_uses_heading():
    est = FixedDistanceEstimator(8)
    g = locate_hazard(28.6, 77.2, 0.0, (0.4, 0.5, 0.6, 0.7), est)   # heading north
    assert g.latitude > 28.6
    assert haversine_m(28.6, 77.2, g.latitude, g.longitude) == pytest.approx(8, abs=0.1)
    g = locate_hazard(28.6, 77.2, None, (0, 0, 1, 1), est)
    assert g.method == "no-heading" and (g.latitude, g.longitude) == (28.6, 77.2)


def test_flat_road_estimator_monotonic():
    est = FlatRoadEstimator(0.42, 2.5, 3, 40, 70)
    near, far = est.estimate((0, 0.8, 1, 0.95)), est.estimate((0, 0.5, 1, 0.55))
    assert near < far <= 40
    assert est.lateral_offset_deg((0.9, 0.5, 1.0, 0.7)) > 0   # object on the right
