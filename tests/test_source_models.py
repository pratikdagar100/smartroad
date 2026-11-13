import json
import time

import pytest
from fastapi.testclient import TestClient

from api.server import create_app
from app import source as src
from app.config import load_config
from app.runtime import SmartRoadRuntime
from detection import model_manager as mm


def test_source_overrides_and_validation(tmp_path):
    assert src.validate({"mode": "nope"})
    assert src.validate({"mode": "video", "video_path": str(tmp_path / "x.mp4")})
    f = tmp_path / "r.mp4"
    f.write_bytes(b"x")
    assert src.validate({"mode": "video", "video_path": str(f)}) is None
    o = src.to_overrides({"mode": "video", "video_path": str(f), "camera_device": "1"})
    assert o["camera"]["backend"] == "video" and o["gps"]["mode"] == "simulated" and o["camera"]["device"] == 1
    assert src.to_overrides({"mode": "demo"})["demo"]["enabled"] is True
    live = src.to_overrides({"mode": "live", "gps_device": "COM3", "model_path": "models/a.onnx"})
    assert live["gps"] == {"device": "COM3", "mode": "serial"} and live["model"]["path"] == "models/a.onnx"
    assert live["camera"]["backend"] == "auto"
    src.save(tmp_path, {"mode": "test"})
    assert src.load(tmp_path)["mode"] == "test" and src.load(tmp_path / "none") == {}


def test_current_mode(cfg):
    assert src.current_mode(cfg) == "test"           # fixture: simulated gps
    assert src.current_mode(load_config(overrides={"demo": {"enabled": True}})) == "demo"
    assert src.current_mode(load_config(overrides={"gps": {"mode": "serial"}})) == "live"


def test_download_rejects_non_https_and_bad_hash(tmp_path):
    with pytest.raises(ValueError):
        mm.download("http://example.com/m.onnx", tmp_path / "m.onnx")
    with pytest.raises(Exception):
        mm.verify_onnx(tmp_path / "missing.onnx", 1)


def test_catalog_and_installed_listing():
    cat = mm.load_catalog(load_config().resolve("models"))
    assert cat and cat[0]["url"].startswith("https://") and len(cat[0]["sha256"]) == 64


def test_real_model_loads_and_verifies():
    cfg = load_config()
    p = cfg.resolve("models/pothole.onnx")
    if not p.exists():
        pytest.skip("model not installed")
    assert "output" in mm.verify_onnx(p, 1)
    from detection.detector import YoloDetector
    import numpy as np
    d = YoloDetector(cfg.model, p)
    assert d.status == "OK"
    assert d.detect(np.zeros((360, 640, 3), np.uint8)) == []      # blank frame: no hallucinated potholes


@pytest.fixture
def rt_api(tmp_path):
    (tmp_path / "models").mkdir()
    cfg = load_config(overrides={
        "database": {"path": str(tmp_path / "t.db")}, "app": {"log_dir": str(tmp_path / "logs")},
        "gps": {"mode": "simulated"}, "camera": {"backend": "image", "source": str(tmp_path / "none.jpg"),
                                                  "reconnect_seconds": 30},
        "model": {"path": str(tmp_path / "missing.onnx")}, "hazards": {"image_dir": str(tmp_path / "img")}})
    rt = SmartRoadRuntime(cfg)
    rt.start()
    yield rt, TestClient(create_app(rt)), tmp_path
    rt.stop()


def test_source_api_saves_choice_and_requests_restart(rt_api, monkeypatch):
    rt, c, _ = rt_api
    monkeypatch.setattr(src, "source_file", lambda root: _tmp_file(rt))
    called = []
    rt.restart_callback = lambda: called.append(1)
    info = c.get("/api/source").json()
    assert info["current"] == "video" and "demo" in info["options"]
    assert c.post("/api/source", json={"mode": "bogus"}).status_code == 422
    assert c.post("/api/source", json={"mode": "video", "video_path": "/no/such.mp4"}).status_code == 422
    r = c.post("/api/source", json={"mode": "demo"})
    assert r.status_code == 200 and r.json()["restarting"]
    time.sleep(1.0)
    assert called == [1]
    assert json.loads(_tmp_file(rt).read_text())["mode"] == "demo"


def _tmp_file(rt):
    return rt.db.path.parent / "ui_source_test.json"


def test_models_api_reports_missing_and_selects(rt_api, monkeypatch):
    rt, c, tmp = rt_api
    monkeypatch.setattr(src, "source_file", lambda root: _tmp_file(rt))
    m = c.get("/api/models").json()
    assert not m["current"]["exists"] and "NOT INSTALLED" in m["current"]["message"].upper()
    assert m["catalog"] and m["job"]["status"] == "idle"
    assert c.post("/api/models/install", json={"id": "nope"}).status_code == 404
    assert c.post("/api/models/select", json={"file": "../../etc/passwd"}).status_code == 422
    real = load_config().resolve("models/pothole.onnx")
    if real.exists():
        out = c.post("/api/models/select", json={"file": "pothole.onnx"}).json()
        assert out["current"]["status"] == "OK"
