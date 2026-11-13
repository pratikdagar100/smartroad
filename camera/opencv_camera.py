"""OpenCV backends: USB webcam, video file, still image."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2

from .base_camera import BaseCamera


class OpenCVCamera(BaseCamera):
    """``mode``: webcam | video | image."""

    def __init__(self, mode: str, device=0, source: str = "", width: int = 1280, height: int = 720,
                 fps: int = 30, **kw):
        super().__init__(**kw)
        self.mode, self.device, self.source = mode, device, source
        self.width, self.height, self.fps = width, height, fps
        self.name = f"opencv-{mode}"
        self._cap = None
        self._img = None
        self._next = 0.0
        self._video_fps = 30.0

    def _open(self) -> None:
        if self.mode == "image":
            p = Path(self.source)
            img = cv2.imread(str(p)) if p.is_file() else None
            if img is None:
                raise FileNotFoundError(f"cannot read image {self.source!r}")
            self._img = img
            return
        if self.mode == "video":
            if not Path(self.source).is_file():
                raise FileNotFoundError(f"video file not found: {self.source!r}")
            cap = cv2.VideoCapture(self.source)
        else:
            dev = self.device
            if isinstance(dev, str) and dev.isdigit():
                dev = int(dev)
            backend = cv2.CAP_DSHOW if sys.platform.startswith("win") and isinstance(dev, int) else cv2.CAP_ANY
            cap = cv2.VideoCapture(dev, backend)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"cannot open {self.mode} source {self.source or self.device!r}")
        self._video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._cap = cap

    def _read(self):
        if self.mode == "image":
            time.sleep(0.1)
            return self._img.copy(), {}
        ok, img = self._cap.read()
        if not ok and self.mode == "video":  # loop the file
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, img = self._cap.read()
        if self.mode == "video":  # pace to the file's frame rate
            self._next = max(self._next + 1.0 / min(60.0, self._video_fps), time.time() - 0.2)
            time.sleep(max(0.0, self._next - time.time()))
        return (img if ok else None), {}

    def _close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
