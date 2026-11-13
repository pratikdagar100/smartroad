import pytest

from app.config import ConfigError, load_config
from detection.tracker import Track
from gps.gps_service import NMEAParser
from verification.fusion import EventEngine
from verification.severity import classify, estimate_severity
from mapping.geolocate import FixedDistanceEstimator
from gps.gps_service import GPSState
import time


# ------------------------------------------------------------------ config
def test_defaults_and_overrides(tmp_path):
    c = load_config(path=tmp_path / "missing.yaml", overrides={"model": {"confidence": 0.6}})
    assert c.model.confidence == 0.6 and c.proximity.warning_distance_m == 50
    assert c.gps.device == "/dev/ttyUSB0" and c.bind_host == "127.0.0.1"


def test_yaml_and_env(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("gps:\n  device: COM7\napp:\n  port: 9000\n")
    monkeypatch.setenv("SMARTROAD__PROXIMITY__WARNING_DISTANCE_M", "80")
    c = load_config(path=p)
    assert c.gps.device == "COM7" and c.app.port == 9000 and c.proximity.warning_distance_m == 80


def test_lan_bind_is_opt_in():
    assert load_config(overrides={"app": {"allow_lan": True}}).bind_host == "0.0.0.0"


@pytest.mark.parametrize("bad", [{"model": {"confidence": 5}}, {"proximity": {"critical_distance_m": 90}},
                                 {"gps": {"mode": "magic"}}, {"hazards": {"merge_radius_m": 0}}])
def test_invalid_config_rejected(bad):
    with pytest.raises(ConfigError):
        load_config(overrides=bad)


def test_repo_config_yaml_is_valid():
    assert load_config().model.classes == ["pothole"]


# ---------------------------------------------------------------- severity
def test_severity_is_monotonic_and_classified(cfg):
    small, lab_s, _ = estimate_severity(cfg.severity, (0.5, 0.5, 0.52, 0.52), 0.5, 30)
    big, lab_b, parts = estimate_severity(cfg.severity, (0.3, 0.5, 0.7, 0.9), 0.95, 5, detection_count=5)
    assert big > small and lab_s == "LOW" and lab_b == "HIGH"
    assert abs(sum(parts.values()) - big) < 0.01
    assert classify(0.5) == "MEDIUM"


def test_missing_imu_does_not_lower_score(cfg):
    base, _, p1 = estimate_severity(cfg.severity, (0.4, 0.5, 0.6, 0.7), 0.9, 10)
    assert "impact" not in p1
    with_imu, _, p2 = estimate_severity(cfg.severity, (0.4, 0.5, 0.6, 0.7), 0.9, 10, impact_g=3.2)
    assert "impact" in p2 and with_imu >= base


# --------------------------------------------------------------------- GPS
def _nmea(body):
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}"


def test_nmea_parsing_and_checksum():
    p = NMEAParser()
    gga = _nmea("GPGGA,123519,2836.800,N,07712.540,E,1,08,0.9,215.0,M,0.0,M,,")
    assert p.feed(gga)
    assert p.lat == pytest.approx(28.61333, abs=1e-4) and p.lon == pytest.approx(77.209, abs=1e-3)
    assert p.sats == 8 and p.hdop == 0.9
    rmc = _nmea("GPRMC,123519,A,2836.800,N,07712.540,E,010.0,084.4,230394,,")
    assert p.feed(rmc) and p.speed == pytest.approx(5.14, abs=0.01) and p.heading == pytest.approx(84.4)
    assert not p.feed(gga[:-1] + "0")            # corrupted checksum rejected
    assert not NMEAParser().feed(_nmea("GPRMC,123519,V,,,,,,,230394,,"))   # no fix


# ------------------------------------------------------------ event engine
def track(conf=0.9):
    return Track(1, 0, "pothole", (300, 200, 340, 230), conf, hits=6, confirmed=True)


def gps(fix=True, speed=10.0):
    return GPSState(28.6, 77.2, 200, speed, 90.0, 2.0, 9, fix, time.time(), "simulated", "OK" if fix else "NO_FIX")


def test_event_requires_gps_and_confidence(cfg):
    eng = EventEngine(cfg, FixedDistanceEstimator(8))
    assert eng.process(track(), gps(fix=False), (640, 360)) is None
    assert eng.process(track(conf=0.3), gps(), (640, 360)) is None
    assert eng.rejected == {"no_gps": 1, "low_confidence": 1}
    e = eng.process(track(), gps(), (640, 360))
    assert e and e.hazard_type == "pothole" and e.longitude > 77.2     # 8 m ahead, heading east


def test_event_cooldown_and_min_distance(cfg):
    eng = EventEngine(cfg, FixedDistanceEstimator(8))
    t0 = time.time()
    assert eng.process(track(), gps(), (640, 360), now=t0)
    assert eng.process(track(), gps(), (640, 360), now=t0 + 1) is None       # same pothole
    assert eng.rejected["recent_duplicate"] == 1
    assert eng.process(track(), gps(), (640, 360), now=t0 + 6)               # cooldown over
