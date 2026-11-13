"""GPS service: the only module that touches the serial device.

The rest of the application calls ``provider.get_state()`` and gets a
``GPSState``.  In hardware mode a missing/failed GPS yields ``fix=False`` -
coordinates are never invented.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, asdict
from typing import Optional

log = logging.getLogger("smartroad.gps")

KNOTS_TO_MS = 0.514444


@dataclass
class GPSState:
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    speed: float = 0.0              # m/s
    heading: Optional[float] = None  # degrees true, None when unknown
    accuracy: Optional[float] = None  # HDOP-derived estimate, metres
    satellites: Optional[int] = None
    fix: bool = False
    timestamp: float = 0.0          # local epoch seconds of last update
    source: str = "none"            # serial | simulated | none
    status: str = "UNAVAILABLE"     # OK | NO_FIX | UNAVAILABLE | SIGNAL_LOST
    message: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["speed_kmh"] = round(self.speed * 3.6, 1)
        return d


def _checksum_ok(sentence: str) -> bool:
    if not sentence.startswith("$") or "*" not in sentence:
        return False
    body, _, cs = sentence[1:].partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(cs.strip()[:2], 16)
    except ValueError:
        return False


def _to_deg(value: str, hemi: str) -> Optional[float]:
    if not value or not hemi:
        return None
    try:
        dot = value.index(".")
        deg = float(value[: dot - 2])
        minutes = float(value[dot - 2:])
    except ValueError:
        return None
    d = deg + minutes / 60.0
    return -d if hemi in ("S", "W") else d


class NMEAParser:
    """Incremental parser for GGA / RMC / GSA / VTG sentences."""

    def __init__(self):
        self.lat = self.lon = self.alt = None
        self.speed = 0.0
        self.heading: Optional[float] = None
        self.hdop: Optional[float] = None
        self.sats: Optional[int] = None
        self.valid = False
        self.fix_time = 0.0

    def feed(self, line: str) -> bool:
        """Parse one sentence; True when position data was updated."""
        line = line.strip()
        if not _checksum_ok(line):
            return False
        f = line[1:].split("*")[0].split(",")
        kind = f[0][-3:]
        try:
            if kind == "RMC" and len(f) >= 9:
                self.valid = f[2] == "A"
                if self.valid:
                    self.lat, self.lon = _to_deg(f[3], f[4]), _to_deg(f[5], f[6])
                    self.speed = float(f[7] or 0) * KNOTS_TO_MS
                    if f[8]:
                        self.heading = float(f[8]) % 360.0
                    self.fix_time = time.time()
                    return self.lat is not None
            elif kind == "GGA" and len(f) >= 10:
                q = int(f[6] or 0)
                self.valid = q > 0
                self.sats = int(f[7] or 0)
                if f[8]:
                    self.hdop = float(f[8])
                if q > 0:
                    self.lat, self.lon = _to_deg(f[2], f[3]), _to_deg(f[4], f[5])
                    if f[9]:
                        self.alt = float(f[9])
                    self.fix_time = time.time()
                    return self.lat is not None
            elif kind == "VTG" and len(f) >= 8:
                if f[1]:
                    self.heading = float(f[1]) % 360.0
                if f[7]:
                    self.speed = float(f[7]) / 3.6
            elif kind == "GSA" and len(f) >= 17 and f[16]:
                self.hdop = float(f[16])
        except (ValueError, IndexError):
            return False
        return False


class GPSProvider:
    """Interface every GPS source implements."""

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def get_state(self) -> GPSState:
        raise NotImplementedError


class SerialGPSService(GPSProvider):
    """Reads NMEA from a USB/UART GPS on its own thread."""

    def __init__(self, device: str, baudrate: int = 9600, max_age_s: float = 3.0,
                 min_satellites: int = 4, reconnect_s: float = 3.0):
        self.device, self.baud = device, baudrate
        self.max_age_s, self.min_sats, self.reconnect_s = max_age_s, min_satellites, reconnect_s
        self._lock = threading.Lock()
        self._state = GPSState(source="serial", status="UNAVAILABLE", message=f"opening {device}")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._parser = NMEAParser()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="gps", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def get_state(self) -> GPSState:
        with self._lock:
            s = GPSState(**asdict(self._state))
        if s.fix and time.time() - s.timestamp > self.max_age_s:
            s.fix, s.status, s.message = False, "SIGNAL_LOST", "no fix update"
        return s

    def _publish(self) -> None:
        p = self._parser
        fix = bool(p.valid and p.lat is not None and (p.sats is None or p.sats >= self.min_sats))
        with self._lock:
            self._state = GPSState(
                latitude=p.lat, longitude=p.lon, altitude=p.alt, speed=p.speed, heading=p.heading,
                accuracy=round(p.hdop * 2.5, 1) if p.hdop else None, satellites=p.sats, fix=fix,
                timestamp=p.fix_time or time.time(), source="serial",
                status="OK" if fix else "NO_FIX", message="" if fix else "waiting for satellites")

    def _set_error(self, msg: str) -> None:
        with self._lock:
            self._state = GPSState(source="serial", status="UNAVAILABLE", message=msg, timestamp=time.time())

    def _run(self) -> None:
        try:
            import serial  # pyserial
        except ImportError:
            self._set_error("pyserial not installed")
            log.error("pyserial missing - GPS unavailable")
            return
        while not self._stop.is_set():
            try:
                log.info("Opening GPS %s @ %s", self.device, self.baud)
                with serial.Serial(self.device, self.baud, timeout=1.0) as ser:
                    log.info("GPS port open")
                    self._set_error("waiting for data")
                    last_data = time.time()
                    while not self._stop.is_set():
                        raw = ser.readline()
                        if not raw:
                            if time.time() - last_data > 10:
                                self._set_error("no NMEA data from device")
                            continue
                        last_data = time.time()
                        line = raw.decode("ascii", errors="ignore")
                        if self._parser.feed(line) or line[3:6] in ("GGA", "RMC"):
                            self._publish()
            except Exception as exc:  # noqa: BLE001 - never let GPS kill the app
                self._set_error(f"{type(exc).__name__}: {exc}")
                log.error("GPS failure: %s (retry in %.0fs)", exc, self.reconnect_s)
                self._stop.wait(self.reconnect_s)


class NullGPS(GPSProvider):
    def __init__(self, message="GPS not configured"):
        self._s = GPSState(status="UNAVAILABLE", message=message)

    def get_state(self) -> GPSState:
        return self._s
