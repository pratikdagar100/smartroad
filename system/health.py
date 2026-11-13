"""System health sampling (CPU/RAM/temp/disk/network).  Cheap and non-blocking."""
from __future__ import annotations

import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Dict, Optional

import psutil


def cpu_temperature() -> Optional[float]:
    try:
        t = Path("/sys/class/thermal/thermal_zone0/temp")
        if t.exists():
            return round(int(t.read_text()) / 1000.0, 1)
        temps = getattr(psutil, "sensors_temperatures", lambda: {})()
        for entries in temps.values():
            if entries:
                return round(entries[0].current, 1)
    except Exception:  # noqa: BLE001
        pass
    return None


def internet_available(timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=timeout):
            return True
    except OSError:
        return False


class HealthMonitor:
    """Samples in a background thread so API calls never block on the network."""

    def __init__(self, data_dir: Path, interval: float = 5.0):
        self.data_dir = data_dir
        self.interval = interval
        self._lock = threading.Lock()
        self._sample: Dict = {}
        self._stop = threading.Event()
        psutil.cpu_percent(None)

    def start(self) -> None:
        self._sample_once()
        threading.Thread(target=self._run, name="health", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self._sample_once()

    def _sample_once(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            du = shutil.disk_usage(self.data_dir)
            vm = psutil.virtual_memory()
            s = {
                "cpu_percent": psutil.cpu_percent(None),
                "ram_percent": vm.percent,
                "ram_used_mb": round(vm.used / 1e6),
                "ram_total_mb": round(vm.total / 1e6),
                "temperature_c": cpu_temperature(),
                "disk_percent": round(du.used / du.total * 100, 1),
                "disk_free_gb": round(du.free / 1e9, 1),
                "network": internet_available(),
                "sampled_at": time.time(),
            }
        except Exception as exc:  # noqa: BLE001
            s = {"error": str(exc)}
        with self._lock:
            self._sample = s

    def get(self) -> Dict:
        with self._lock:
            return dict(self._sample)
