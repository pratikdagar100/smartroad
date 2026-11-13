"""Select a camera backend from configuration."""
from __future__ import annotations

import logging
import platform
from typing import Optional

from .base_camera import BaseCamera

log = logging.getLogger("smartroad.camera")


def _is_pi() -> bool:
    try:
        with open("/proc/device-tree/model", "rb") as fh:
            return b"Raspberry Pi" in fh.read()
    except OSError:
        return False


def make_camera(cfg, simulator=None, demo_potholes=None) -> BaseCamera:
    c = cfg.camera
    backend = str(c.backend).lower()
    common = dict(reconnect_s=float(c.reconnect_seconds), flip=bool(c.flip))
    if backend == "auto":
        if cfg.demo_enabled:
            backend = "demo"
        elif _is_pi() and str(c.device) in ("0", "picamera2", "csi"):
            backend = "picamera2"
        else:
            backend = "opencv"
    log.info("Camera backend: %s (platform %s)", backend, platform.system())

    if backend == "demo":
        if simulator is None:
            raise RuntimeError("demo camera needs the GPS simulator (run with --demo)")
        from .demo_camera import DemoCamera
        return DemoCamera(simulator, demo_potholes or [], cfg.geolocation, fps=20, **common)
    if backend == "picamera2":
        from .picamera2_camera import Picamera2Camera
        return Picamera2Camera(width=c.width, height=c.height, fps=c.fps, **common)
    from .opencv_camera import OpenCVCamera
    if backend in ("video", "image"):
        return OpenCVCamera(backend, source=str(c.source), **common)
    return OpenCVCamera("webcam", device=c.device, width=c.width, height=c.height, fps=c.fps, **common)
