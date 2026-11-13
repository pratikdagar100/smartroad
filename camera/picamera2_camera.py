"""Raspberry Pi camera via Picamera2 (libcamera).  Imported lazily - Pi only."""
from __future__ import annotations

from .base_camera import BaseCamera


class Picamera2Camera(BaseCamera):
    name = "picamera2"

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 30, **kw):
        super().__init__(**kw)
        self.width, self.height, self.fps = width, height, fps
        self._cam = None

    def _open(self) -> None:
        try:
            from picamera2 import Picamera2
        except ImportError as exc:
            raise RuntimeError("picamera2 not installed (sudo apt install python3-picamera2; "
                               "venv needs --system-site-packages)") from exc
        cam = Picamera2()
        cfg = cam.create_video_configuration(
            main={"size": (self.width, self.height), "format": "BGR888"},
            controls={"FrameRate": float(self.fps)})
        cam.configure(cfg)
        cam.start()
        self._cam = cam

    def _read(self):
        # "BGR888" in libcamera is already OpenCV channel order
        return self._cam.capture_array("main"), {}

    def _close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop()
                self._cam.close()
            finally:
                self._cam = None
