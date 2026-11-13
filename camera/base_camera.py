"""Camera abstraction.  The pipeline only ever sees ``BaseCamera``."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

log = logging.getLogger("smartroad.camera")

ST_CONNECTING, ST_OK, ST_ERROR = "CONNECTING", "OK", "ERROR"


@dataclass
class Frame:
    image: np.ndarray            # BGR
    timestamp: float
    seq: int
    meta: dict = field(default_factory=dict)


class BaseCamera:
    """Background-capture camera: always keeps only the newest frame (no backlog)."""

    name = "base"

    def __init__(self, reconnect_s: float = 3.0, flip: bool = False):
        self.reconnect_s = reconnect_s
        self.flip = flip
        self.status = ST_CONNECTING
        self.message = ""
        self.capture_fps = 0.0
        self._frame: Optional[Frame] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._seq = 0

    # ---- backend hooks -----------------------------------------------------
    def _open(self) -> None:
        """Open the device. Raise on failure."""
        raise NotImplementedError

    def _read(self):
        """Return ``(image_or_None, meta_dict)``."""
        raise NotImplementedError

    def _close(self) -> None:
        pass

    # ---- public API --------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"camera-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        try:
            self._close()
        except Exception:  # noqa: BLE001
            pass

    def get_frame(self) -> Optional[Frame]:
        with self._lock:
            return self._frame

    # ---- thread ------------------------------------------------------------
    def _set(self, status: str, msg: str = "") -> None:
        if status != self.status or msg != self.message:
            log.info("Camera %s: %s %s", self.name, status, msg)
        self.status, self.message = status, msg

    def _run(self) -> None:
        count, t0 = 0, time.time()
        while not self._stop.is_set():
            try:
                self._set(ST_CONNECTING, "opening")
                self._open()
                self._set(ST_OK)
                fails = 0
                while not self._stop.is_set():
                    img, meta = self._read()
                    if img is None:
                        fails += 1
                        if fails > 30:
                            raise RuntimeError("camera stopped delivering frames")
                        time.sleep(0.03)
                        continue
                    fails = 0
                    if self.flip:
                        img = img[::-1, ::-1].copy()
                    self._seq += 1
                    with self._lock:
                        self._frame = Frame(img, time.time(), self._seq, meta or {})
                    count += 1
                    if time.time() - t0 >= 2:
                        self.capture_fps = count / (time.time() - t0)
                        count, t0 = 0, time.time()
            except Exception as exc:  # noqa: BLE001 - camera must never kill the app
                self._set(ST_ERROR, f"{type(exc).__name__}: {exc}")
                log.error("Camera error: %s", exc)
                with self._lock:
                    self._frame = None
                try:
                    self._close()
                except Exception:  # noqa: BLE001
                    pass
                self._stop.wait(self.reconnect_s)
