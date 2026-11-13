"""Hardware / software self-test:  python -m app.hardware_test [--gps-seconds 5]"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import tempfile
import time

from .config import load_config

OK, WARN, FAIL = "[OK]", "[WARNING]", "[FAIL]"


def check_camera(cfg):
    from camera.factory import make_camera
    cam = make_camera(cfg)
    cam.start()
    try:
        for _ in range(60):
            f = cam.get_frame()
            if f is not None:
                h, w = f.image.shape[:2]
                return OK, f"Camera ({cam.name}) {w}x{h}"
            time.sleep(0.1)
        return FAIL, f"Camera ({cam.name}): {cam.message or 'no frames within 6 s'}"
    finally:
        cam.stop()


def check_gps(cfg, seconds: float):
    if cfg.gps.mode == "simulated":
        return WARN, "GPS is simulated (gps.mode: simulated) - no real receiver tested"
    from gps.gps_service import SerialGPSService
    g = SerialGPSService(cfg.gps.device, cfg.gps.baudrate)
    g.start()
    try:
        end = time.time() + seconds
        s = g.get_state()
        while time.time() < end:
            s = g.get_state()
            if s.fix:
                return OK, f"GPS fix {s.latitude:.5f},{s.longitude:.5f} ({s.satellites} sats)"
            time.sleep(0.5)
        if s.status == "UNAVAILABLE":
            return FAIL, f"GPS {cfg.gps.device}: {s.message}"
        return WARN, f"GPS {cfg.gps.device} responds but has no fix yet (go outdoors, wait 1-5 min)"
    finally:
        g.stop()


def check_database(cfg):
    from database.db import Database
    d = tempfile.mkdtemp()
    try:
        db = Database(os.path.join(d, "t.db"))
        ok = db.ping() and db.statistics()["hazards_total"] == 0
        db.close()
        return (OK, f"Database (schema ok, main file {cfg.db_path})") if ok else (FAIL, "Database check failed")
    except Exception as exc:  # noqa: BLE001
        return FAIL, f"Database: {exc}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_model(cfg):
    p = cfg.resolve(cfg.model.path)
    if not p.exists():
        return WARN, f"AI model not installed at {p} (see models/README.md)"
    from detection.detector import YoloDetector
    det = YoloDetector(cfg.model, p)
    return (OK, f"AI model {p.name} ({det.backend}, input {det.size})") if det.status == "OK" \
        else (FAIL, f"AI model failed: {det.message}")


def check_resources(cfg):
    import psutil
    from system.health import cpu_temperature
    vm = psutil.virtual_memory()
    t = cpu_temperature()
    msg = f"CPU {psutil.cpu_count()} cores, RAM {vm.total / 1e9:.1f} GB ({vm.percent:.0f}% used)"
    if t is not None:
        msg += f", {t:.0f} C"
    return (WARN if (t and t > 80) or vm.percent > 90 else OK), msg


def check_display():
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return WARN, "Display: no DISPLAY/WAYLAND_DISPLAY (normal over SSH; kiosk needs a desktop session)"
    return OK, "Display available"


def check_internet():
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=1.5).close()
        return OK, "Internet reachable (not required)"
    except OSError:
        return WARN, "Internet unavailable (SmartRoad works fully offline)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--gps-seconds", type=float, default=5.0)
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    results = []
    for name, fn in (("camera", lambda: check_camera(cfg)), ("gps", lambda: check_gps(cfg, a.gps_seconds)),
                     ("display", check_display), ("database", lambda: check_database(cfg)),
                     ("resources", lambda: check_resources(cfg)), ("model", lambda: check_model(cfg)),
                     ("internet", check_internet)):
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001
            results.append((FAIL, f"{name}: {type(exc).__name__}: {exc}"))
    for status, msg in results:
        print(f"{status:<10} {msg}")
    return 1 if any(s == FAIL for s, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
