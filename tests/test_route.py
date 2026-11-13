from mapping.geo import destination_point
from mapping.route import RouteRecorder, find_repeated_route, route_overlap


def line(lat, lon, n, step, brg=90):
    pts = [(lat, lon)]
    for _ in range(n):
        pts.append(destination_point(*pts[-1], brg, step))
    return pts


def test_overlap_same_and_different():
    a = line(28.6, 77.2, 40, 10)
    assert route_overlap(a, a) == 1.0
    assert route_overlap(line(28.60005, 77.2, 40, 10), a, 25) == 1.0     # ~5 m offset
    assert route_overlap(line(28.7, 77.2, 40, 10), a, 25) == 0.0


def test_recorder_thresholds(db):
    r = RouteRecorder(db, db.ensure_vehicle("v"), interval_s=1.0, min_distance_m=3.0)
    t = 1000.0
    assert r.update(28.6, 77.2, now=t)
    assert not r.update(28.6, 77.2, now=t + 0.5)                       # too soon
    assert not r.update(28.6, 77.2, now=t + 2)                         # too close (duplicate)
    p = destination_point(28.6, 77.2, 90, 10)
    assert r.update(*p, now=t + 3)
    assert not r.update(None, None, now=t + 4)
    assert len(r.points) == 2 and 9 < r.distance_m < 11
    r.finish()
    assert db.list_routes()[0]["point_count"] == 2


def test_new_route_after_gap(db):
    r = RouteRecorder(db, db.ensure_vehicle("v"), gap_new_route_s=60)
    r.update(28.6, 77.2, now=0)
    first = r.route_id
    r.update(28.601, 77.2, now=1000)
    assert r.route_id != first


def test_repeat_route_detection(db):
    vid = db.ensure_vehicle("v")
    old = line(28.6, 77.2, 60, 10)
    rid = db.start_route(vid)
    for p in old:
        db._insert_track(rid, "t", p[0], p[1], None, None, None, None)
    db.end_route(rid, 600, len(old))
    m = find_repeated_route(db, line(28.60005, 77.2, 40, 10), exclude_route_id=None)
    assert m and m["route_id"] == rid and m["overlap"] >= 0.9
    assert find_repeated_route(db, line(29.0, 78.0, 40, 10), None) is None
