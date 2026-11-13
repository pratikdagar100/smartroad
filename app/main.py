"""SmartRoad entry point.

    python -m app.main                     # real hardware (Pi) or webcam on a PC
    python -m app.main --demo              # full simulated demonstration
    python -m app.main --camera webcam --gps simulated
    python -m app.main --camera video --source demo/road.mp4 --gps simulated
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import webbrowser

from .config import ConfigError, load_config
from .logging_setup import setup_logging

log = logging.getLogger("smartroad")
RESTART = 75


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="smartroad", description="SmartRoad road-hazard detection & route memory")
    p.add_argument("--config", help="path to config.yaml")
    p.add_argument("--demo", action="store_true", help="simulated vehicle, camera, GPS and potholes")
    p.add_argument("--camera", choices=["auto", "webcam", "opencv", "picamera2", "video", "image", "demo"],
                   help="camera backend")
    p.add_argument("--source", help="file for --camera video/image")
    p.add_argument("--device", help="webcam index or device path")
    p.add_argument("--gps", choices=["serial", "simulated"], help="GPS source")
    p.add_argument("--gps-device", help="serial device, e.g. /dev/ttyUSB0 or COM3")
    p.add_argument("--model", help="model path (.onnx / .tflite)")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--speed", type=float, help="demo time scale (2 = twice as fast)")
    p.add_argument("--open-browser", action="store_true", help="open the UI in the default browser")
    p.add_argument("--init-db", action="store_true", help="initialise the database and exit")
    return p.parse_args(argv)


def build_overrides(a: argparse.Namespace) -> dict:
    o: dict = {}
    def put(sec, key, val):
        if val is not None:
            o.setdefault(sec, {})[key] = val
    if a.demo:
        put("demo", "enabled", True)
    if a.camera:
        put("camera", "backend", "opencv" if a.camera == "webcam" else a.camera)
        if a.camera == "webcam":
            put("camera", "backend", "opencv")
    put("camera", "source", a.source)
    if a.source and not a.camera:
        put("camera", "backend", "video")
    put("camera", "device", int(a.device) if a.device and a.device.isdigit() else a.device)
    put("gps", "mode", a.gps)
    put("gps", "device", a.gps_device)
    put("model", "path", a.model)
    put("app", "host", a.host)
    put("app", "port", a.port)
    put("demo", "time_scale", a.speed)
    return o


SOURCE_FLAGS = ("demo", "camera", "source", "device", "gps", "gps_device")


def _merge(a: dict, b: dict) -> dict:
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in a.items()}
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def run_once(a: argparse.Namespace, cold_start: bool) -> int:
    """Run one server lifetime. Returns RESTART when the UI asked for a source switch."""
    from . import source as source_mod
    explicit = any(getattr(a, k) for k in SOURCE_FLAGS)
    root = load_config(a.config).root
    ui = source_mod.load(root)
    if explicit and cold_start:
        ov = build_overrides(a)                      # command line wins on a cold start
        if ui.get("model_path") and not a.model:
            ov = _merge(ov, {"model": {"path": ui["model_path"]}})
    else:
        b = argparse.Namespace(**vars(a))
        for k in SOURCE_FLAGS:
            setattr(b, k, None if k != "demo" else False)
        ov = _merge(build_overrides(b), source_mod.to_overrides(ui))
    try:
        cfg = load_config(a.config, ov)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    setup_logging(cfg.resolve(cfg.app.log_dir), cfg.app.log_level)
    log.info("=== SmartRoad starting (demo=%s) ===", cfg.demo_enabled)

    from .runtime import SmartRoadRuntime
    rt = SmartRoadRuntime(cfg)
    if a.init_db:
        print(f"Database ready: {cfg.db_path}")
        rt.db.close()
        return 0

    import uvicorn
    from api.server import create_app
    app = create_app(rt)
    host, port = cfg.bind_host, int(cfg.app.port)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    restart = {"flag": False}

    def trigger_restart() -> None:
        restart["flag"] = True
        server.should_exit = True
    rt.restart_callback = trigger_restart

    rt.start()
    url = f"http://127.0.0.1:{port}/"
    if a.open_browser and cold_start:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    print(f"\n  SmartRoad running at {url}   mode={rt.source_mode.upper()}   (Ctrl+C to stop)\n")
    try:
        server.run()
    finally:
        rt.stop()
    return RESTART if restart["flag"] else 0


def main(argv=None) -> int:
    a = parse_args(argv)
    cold = True
    while True:
        rc = run_once(a, cold)
        if rc != RESTART:
            return rc
        log.info("Restarting to apply the new source selection")
        cold = False
        a.init_db = False


if __name__ == "__main__":
    sys.exit(main())
