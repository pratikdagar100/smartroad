"""API, demo-mode and hardware-honesty tests (start the real runtime)."""
import time

import pytest
from fastapi.testclient import TestClient

from api.server import create_app
from app.config import load_config
from app.runtime import SmartRoadRuntime


def wait_for(fn, timeout=40.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


@pytest.fixture
def demo_rt(tmp_path):
    cfg = load_config(overrides={
        "demo": {"enabled": True, "db_path": str(tmp_path / "demo.db"), "time_scale": 4.0},
        "hazards": {"image_dir": str(tmp_path / "img")},
        "app": {"log_dir": str(tmp_path / "logs")},
    })
    rt = SmartRoadRuntime(cfg)
    rt.start()
    yield rt
    rt.stop()


def test_demo_mode_detects_saves_and_warns(demo_rt):
    rt = demo_rt
    assert wait_for(lambda: rt.state.counters["events_created"] >= 1), "no pothole was detected in demo"
    created = next(h for h in rt.db.get_hazards() if h.source == "camera")
    assert created.image_path and (rt.cfg.root / created.image_path).exists() or created.image_path
    # drive on: a stored (pre-seeded) hazard must trigger the KNOWN warning at some point
    seen = wait_for(lambda: any(e["kind"] == "known_warning" for e in rt.state.snapshot()["events"]), 60)
    assert seen, "KNOWN POTHOLE AHEAD never triggered"
    # one physical pothole must never produce a pile of records
    hs = rt.db.get_hazards()
    assert len(hs) <= len(rt.demo_potholes) + 1


def test_api_endpoints(demo_rt):
    rt = demo_rt
    c = TestClient(create_app(rt))
    wait_for(lambda: rt.state.snapshot()["gps"].get("fix"))
    assert c.get("/api/status").json()["mode"] == "demo"
    assert c.get("/api/gps").json()["source"] == "simulated"
    assert isinstance(c.get("/api/hazards").json(), list)
    assert c.get("/api/hazards?status=BOGUS").status_code == 400
    assert c.get("/api/statistics").json()["hazards_total"] >= 2        # pre-seeded
    assert "config" in c.get("/api/settings").json()
    assert c.get("/api/health").json()["db"]["ok"]
    assert c.get("/api/routes").status_code == 200 and c.get("/api/route").status_code == 200
    assert c.get("/").status_code == 200
    nb = c.get("/api/hazards/nearby", params={"lat": 28.6176, "lon": 77.2158, "radius": 200}).json()
    assert nb and nb[0]["distance_m"] <= 200

    r = c.post("/api/hazards", json={"latitude": 28.7, "longitude": 77.1, "severity": "HIGH"})
    assert r.status_code == 201 and r.json()["created"]
    hid = r.json()["hazard"]["id"]
    dup = c.post("/api/hazards", json={"latitude": 28.700001, "longitude": 77.100001}).json()
    assert not dup["created"] and dup["hazard"]["id"] == hid
    assert c.post(f"/api/hazards/{hid}/status", json={"status": "FIXED"}).json()["status"] == "FIXED"
    assert c.post(f"/api/hazards/{hid}/status", json={"status": "NOPE"}).status_code == 422
    assert c.post("/api/hazards/9999/status", json={"status": "FIXED"}).status_code == 404
    assert c.post("/api/hazards", json={"latitude": 0, "longitude": 0}).status_code == 422

    assert c.post("/api/settings", json={"proximity.warning_distance_m": 60}).json()["applied"]
    assert c.post("/api/settings", json={"gps.device": "x"}).status_code == 422
    assert c.post("/api/settings", json={"proximity.critical_distance_m": 999}).status_code == 422

    with c.websocket_connect("/ws/live") as ws:
        msg = ws.receive_json()
        assert {"gps", "fps", "detections", "proximity", "subsystems"} <= msg.keys()


def test_token_protects_writes(demo_rt, monkeypatch):
    monkeypatch.setenv("SMARTROAD_API_TOKEN", "s3cret")
    c = TestClient(create_app(demo_rt))
    body = {"latitude": 28.7, "longitude": 77.1}
    assert c.post("/api/hazards", json=body).status_code == 401
    assert c.post("/api/hazards", json=body, headers={"Authorization": "Bearer s3cret"}).status_code == 201
    assert c.get("/api/hazards").status_code == 200       # reads stay open (local only)


def test_hardware_mode_never_fabricates(tmp_path):
    """No GPS device + no camera + no model => honest error states, zero fake data."""
    cfg = load_config(overrides={
        "database": {"path": str(tmp_path / "hw.db")},
        "app": {"log_dir": str(tmp_path / "logs")},
        "gps": {"mode": "serial", "device": "/dev/definitely-not-here", "reconnect_seconds": 30},
        "camera": {"backend": "video", "source": str(tmp_path / "nope.mp4"), "reconnect_seconds": 30},
        "model": {"path": str(tmp_path / "missing.onnx")},
        "hazards": {"image_dir": str(tmp_path / "img")},
    })
    rt = SmartRoadRuntime(cfg)
    rt.start()
    try:
        assert wait_for(lambda: rt.state.subsystems.get("camera", {}).get("status") == "ERROR", 15)
        assert wait_for(lambda: rt.state.subsystems.get("gps", {}).get("status") == "ERROR", 15)
        snap = rt.state.snapshot()
        assert snap["mode"] == "hardware" and not snap["gps"]["fix"]
        assert snap["gps"]["latitude"] is None
        assert "AI MODEL NOT INSTALLED" in snap["subsystems"]["ai"]["message"]
        assert snap["detections"] == [] and rt.db.get_hazards() == []
        assert rt.state.get_jpeg()        # placeholder frame ("CAMERA ERROR") keeps the UI alive
    finally:
        rt.stop()


def test_simulated_gps_is_deterministic_route_following():
    from gps.gps_simulator import GPSSimulator
    route = [(28.6, 77.2), (28.601, 77.2)]
    sim = GPSSimulator(route, speed_kmh=36, ping_pong=True)
    s = sim.step(5.0)                                   # 10 m/s * 5 s
    assert s.fix and s.heading == pytest.approx(0, abs=1) and 28.6 < s.latitude < 28.601
    sim.step(1000)
    assert sim.get_state().fix
