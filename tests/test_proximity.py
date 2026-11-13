from database.models import Hazard
from mapping.proximity import ProximityEngine

LAT, LON = 28.61315, 77.03135


def hz(i, lat, lon, status="ACTIVE"):
    return Hazard(i, lat, lon, "pothole", "LOW", 0.2, 0.9, None, "t", "t", 1, status, "camera", None, None)


def make(**kw):
    e = ProximityEngine(warning_m=50, critical_m=20, **kw)
    e.load([hz(1, LAT, LON)])
    return e


def test_far_no_warning():
    r = make().update(28.6100, 77.0300, 45, 10)
    assert r.warning is None and r.nearby == []


def test_warning_then_critical_once_each():
    e = make()
    lat0 = LAT - 0.00035                                     # ~39 m south, heading north
    r = e.update(lat0, LON, 0, 10)
    assert r.warning and r.warning["level"] == "WARNING" and len(r.new_triggers) == 1
    assert 30 < r.warning["distance_m"] < 45
    r = e.update(lat0 + 0.00001, LON, 0, 10)                 # next GPS tick: no re-trigger
    assert r.warning and r.new_triggers == []
    r = e.update(LAT - 0.00012, LON, 0, 10)                  # ~13 m
    assert r.warning["level"] == "CRITICAL" and len(r.new_triggers) == 1
    r = e.update(LAT - 0.00011, LON, 0, 10)
    assert r.new_triggers == []


def test_not_ahead_no_warning_and_cleared_after_passing():
    e = make()
    assert e.update(LAT - 0.0003, LON, 0, 10).warning        # approaching
    r = e.update(LAT + 0.0002, LON, 0, 10)                   # passed, driving away
    assert r.warning is None
    assert make().update(LAT - 0.0003, LON, 180, 10).warning is None   # heading away


def test_hysteresis_rearm():
    e = make()
    e.update(LAT - 0.00035, LON, 0, 10)                      # triggers
    r = e.update(LAT - 0.00050, LON, 0, 10)                  # ~55 m: inside hysteresis band
    assert r.new_triggers == [] and r.warning
    r = e.update(LAT - 0.0008, LON, 0, 10)                   # ~89 m: cleared
    assert r.warning is None
    assert e.update(LAT - 0.00035, LON, 0, 10).new_triggers  # re-armed


def test_quiet_for_new_hazard_and_fixed_ignored():
    e = make()
    e.quiet(1, 60)
    assert e.update(LAT - 0.00035, LON, 0, 10).warning is None
    e2 = ProximityEngine()
    e2.load([hz(1, LAT, LON, "FIXED")])
    assert e2.update(LAT - 0.00035, LON, 0, 10).warning is None


def test_no_gps_returns_empty():
    r = make().update(None, None, None, 0)
    assert r.warning is None and r.nearby == []


def test_stationary_ignores_heading():
    assert make().update(LAT - 0.00035, LON, None, 0.0).warning is not None
