"""Optional MPU6050 impact sensor.  The app runs fine without it."""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from typing import Callable, Deque, Optional, Tuple

log = logging.getLogger("smartroad.imu")


class IMUMonitor:
    """Detects vehicle impacts (acceleration deviating from 1 g)."""

    def __init__(self, bus: int = 1, address: int = 0x68, impact_g: float = 1.6,
                 on_impact: Optional[Callable[[float], None]] = None):
        self.bus_id, self.address, self.impact_g = bus, address, impact_g
        self.on_impact = on_impact
        self.status = "OFF"
        self.message = ""
        self._peaks: Deque[Tuple[float, float]] = deque(maxlen=200)
        self._stop = threading.Event()

    def recent_peak(self, window_s: float) -> Optional[float]:
        now = time.time()
        vals = [g for t, g in self._peaks if now - t <= window_s]
        return max(vals) if vals else None

    def feed(self, g_magnitude: float, now: Optional[float] = None) -> None:
        """Feed an acceleration magnitude in g (also used by tests)."""
        dev = abs(g_magnitude - 1.0) + 1.0
        if dev >= self.impact_g:
            self._peaks.append((time.time() if now is None else now, dev))
            if self.on_impact:
                self.on_impact(dev)

    def start(self) -> None:
        threading.Thread(target=self._run, name="imu", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            from smbus2 import SMBus
            bus = SMBus(self.bus_id)
            bus.write_byte_data(self.address, 0x6B, 0)  # wake MPU6050
            self.status = "OK"
        except Exception as exc:  # noqa: BLE001
            self.status, self.message = "UNAVAILABLE", f"{type(exc).__name__}: {exc}"
            log.warning("IMU unavailable (%s) - continuing without it", exc)
            return

        def word(reg: int) -> int:
            hi, lo = bus.read_i2c_block_data(self.address, reg, 2)
            v = (hi << 8) | lo
            return v - 65536 if v > 32767 else v

        while not self._stop.wait(0.02):
            try:
                ax, ay, az = (word(r) / 16384.0 for r in (0x3B, 0x3D, 0x3F))
                self.feed(math.sqrt(ax * ax + ay * ay + az * az))
            except Exception as exc:  # noqa: BLE001
                self.status, self.message = "ERROR", str(exc)
                self._stop.wait(1.0)
