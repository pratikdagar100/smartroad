import time

import pytest

from database.db import Database
from mapping.geo import destination_point


def ev(db, lat, lon, **kw):
    base = dict(lat=lat, lon=lon, hazard_type="pothole", confidence=0.8, severity="LOW", severity_score=0.2,
                merge_radius_m=4.0)
    base.update(kw)
    return db.record_hazard_event(**base)


def test_schema_tables(db):
    names = {r[0] for r in db._query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"vehicles", "routes", "hazards", "detections", "gps_tracks", "settings"} <= names
    assert db.version == 1


def test_create_hazard(db):
    h, created = ev(db, 28.6129, 77.0312)
    assert created and h.id == 1 and h.status == "ACTIVE" and h.detection_count == 1


def test_merge_within_radius_spec_example(db):
    h1, c1 = ev(db, 28.612900, 77.031200)
    h2, c2 = ev(db, 28.612905, 77.031205, confidence=0.95)
    assert c1 and not c2 and h1.id == h2.id
    assert h2.detection_count == 2 and h2.confidence == 0.95
    assert len(db.get_hazards()) == 1
    assert len(db._query("SELECT * FROM detections")) == 2


def test_no_merge_outside_radius_or_other_type(db):
    ev(db, 28.6129, 77.0312)
    lat, lon = destination_point(28.6129, 77.0312, 90, 20)
    assert ev(db, lat, lon)[1]
    assert ev(db, 28.6129, 77.0312, hazard_type="crack")[1]
    assert len(db.get_hazards()) == 3


def test_fixed_reactivates_on_redetection(db):
    h, _ = ev(db, 28.6129, 77.0312)
    db.set_hazard_status(h.id, "FIXED")
    h2, created = ev(db, 28.6129, 77.0312)
    assert not created and h2.status == "ACTIVE"


def test_status_validation(db):
    h, _ = ev(db, 28.6, 77.2)
    with pytest.raises(ValueError):
        db.set_hazard_status(h.id, "BROKEN")
    assert db.set_hazard_status(h.id, "unknown") and db.get_hazard(h.id).status == "UNKNOWN"
    assert not db.set_hazard_status(999, "FIXED")


def test_settings_roundtrip(db):
    db.set_setting("a", {"x": 1})
    db.set_setting("a", 5)
    assert db.get_setting("a") == 5 and db.get_setting("zz", "d") == "d"


def test_async_track_points_and_routes(db):
    vid = db.ensure_vehicle("car")
    assert db.ensure_vehicle("car") == vid
    rid = db.start_route(vid)
    for i in range(5):
        db.enqueue_track_point(rid, 28.6 + i * 1e-4, 77.2)
    db.flush()
    time.sleep(0.3)
    assert len(db.get_route_points(rid)) == 5
    db.end_route(rid, 40.0, 5)
    assert db.list_routes()[0]["ended_at"]


def test_statistics(db):
    ev(db, 28.6, 77.2)
    s = db.statistics()
    assert s["hazards_total"] == 1 and s["by_status"] == {"ACTIVE": 1}


def test_migrations_idempotent(tmp_path):
    Database(tmp_path / "x.db").close()
    d = Database(tmp_path / "x.db")
    assert d.version == 1
    d.close()
