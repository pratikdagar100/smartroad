"""SQLite access layer (thread-safe, offline, WAL)."""
from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mapping.geo import bbox_around, haversine_m
from .migrations import migrate
from .models import STATUSES, STATUS_ACTIVE, Hazard, iso_to_epoch, now_iso

log = logging.getLogger("smartroad.db")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.version = migrate(self._conn)
        self._q: "queue.Queue[Optional[Tuple[str, tuple]]]" = queue.Queue(maxsize=2000)
        self._writer = threading.Thread(target=self._writer_loop, name="db-writer", daemon=True)
        self._writer.start()
        self.last_error: Optional[str] = None
        log.info("Database ready at %s (schema v%s)", self.path, self.version)

    # ------------------------------------------------------------ plumbing
    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        try:
            self._q.put(None, timeout=1)
            self._writer.join(timeout=3)
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def ping(self) -> bool:
        try:
            self._query("SELECT 1")
            return True
        except sqlite3.Error as exc:
            self.last_error = str(exc)
            return False

    # ----------------------------------------------------- async write queue
    def _writer_loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            kind, args = item
            try:
                if kind == "track":
                    self._insert_track(*args)
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                log.error("DB async write failed: %s", exc)

    def enqueue_track_point(self, route_id: int, lat: float, lon: float, altitude=None,
                            speed=None, heading=None, accuracy=None) -> bool:
        try:
            self._q.put_nowait(("track", (route_id, now_iso(), lat, lon, altitude, speed, heading, accuracy)))
            return True
        except queue.Full:
            log.warning("DB write queue full - dropping GPS track point")
            return False

    def flush(self, timeout: float = 3.0) -> None:
        end = time.time() + timeout
        while not self._q.empty() and time.time() < end:
            time.sleep(0.02)

    def _insert_track(self, route_id, ts, lat, lon, alt, speed, heading, acc) -> None:
        self._exec("INSERT INTO gps_tracks(route_id,ts,latitude,longitude,altitude,speed,heading,accuracy)"
                   " VALUES (?,?,?,?,?,?,?,?)", (route_id, ts, lat, lon, alt, speed, heading, acc))

    # ------------------------------------------------------------- vehicles
    def ensure_vehicle(self, name: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT id FROM vehicles WHERE name=?", (name,)).fetchone()
            if row:
                return row["id"]
            cur = self._conn.execute("INSERT INTO vehicles(name,created_at) VALUES (?,?)", (name, now_iso()))
            self._conn.commit()
            return int(cur.lastrowid)

    # --------------------------------------------------------------- routes
    def start_route(self, vehicle_id: int, name: Optional[str] = None) -> int:
        ts = now_iso()
        name = name or f"Route {ts[:16].replace('T', ' ')}"
        return int(self._exec("INSERT INTO routes(vehicle_id,name,started_at) VALUES (?,?,?)",
                              (vehicle_id, name, ts)).lastrowid)

    def end_route(self, route_id: int, distance_m: float, point_count: int) -> None:
        self._exec("UPDATE routes SET ended_at=?, distance_m=?, point_count=? WHERE id=?",
                   (now_iso(), distance_m, point_count, route_id))

    def update_route_progress(self, route_id: int, distance_m: float, point_count: int) -> None:
        self._exec("UPDATE routes SET distance_m=?, point_count=? WHERE id=?",
                   (distance_m, point_count, route_id))

    def list_routes(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self._query("SELECT * FROM routes ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def get_route_points(self, route_id: int, stride: int = 1, limit: int = 20000) -> List[Tuple[float, float]]:
        rows = self._query("SELECT latitude, longitude FROM gps_tracks WHERE route_id=? ORDER BY id LIMIT ?",
                           (route_id, limit))
        pts = [(r["latitude"], r["longitude"]) for r in rows]
        return pts[::max(1, stride)]

    # -------------------------------------------------------------- hazards
    def find_nearby_hazard(self, lat: float, lon: float, hazard_type: str,
                           radius_m: float) -> Optional[Hazard]:
        a, b, c, d = bbox_around(lat, lon, radius_m)
        rows = self._query("SELECT * FROM hazards WHERE hazard_type=? AND latitude BETWEEN ? AND ?"
                           " AND longitude BETWEEN ? AND ?", (hazard_type, a, b, c, d))
        best, best_d = None, radius_m
        for r in rows:
            dist = haversine_m(lat, lon, r["latitude"], r["longitude"])
            if dist <= best_d:
                best, best_d = r, dist
        return Hazard.from_row(best) if best else None

    def record_hazard_event(self, *, lat: float, lon: float, hazard_type: str, confidence: float,
                            severity: str, severity_score: float, merge_radius_m: float,
                            source: str = "camera", heading: Optional[float] = None,
                            speed: Optional[float] = None, route_id: Optional[int] = None,
                            bbox: Optional[List[float]] = None, vehicle_pos: Optional[Tuple[float, float]] = None,
                            distance_m: Optional[float] = None, impact: Optional[float] = None,
                            verified: bool = False, ts: Optional[str] = None) -> Tuple[Hazard, bool]:
        """Create a hazard, or merge into an existing one within ``merge_radius_m``.

        Returns ``(hazard, created)``.
        """
        ts = ts or now_iso()
        with self._lock:
            existing = self.find_nearby_hazard(lat, lon, hazard_type, merge_radius_m)
            if existing:
                n = existing.detection_count
                w = min(n, 20)
                new_lat = (existing.latitude * w + lat) / (w + 1)
                new_lon = (existing.longitude * w + lon) / (w + 1)
                score = max(existing.severity_score, severity_score)
                sev = severity if score == severity_score else existing.severity
                self._conn.execute(
                    "UPDATE hazards SET latitude=?, longitude=?, confidence=?, severity=?, severity_score=?,"
                    " last_detected=?, detection_count=detection_count+1, status=?, heading=COALESCE(?,heading),"
                    " speed=COALESCE(?,speed), verified=MAX(verified,?) WHERE id=?",
                    (new_lat, new_lon, max(existing.confidence, confidence), sev, score, ts,
                     STATUS_ACTIVE, heading, speed, int(verified), existing.id))
                hid, created = existing.id, False
                log.info("Merged detection into hazard #%s (count=%s)", hid, n + 1)
            else:
                cur = self._conn.execute(
                    "INSERT INTO hazards(latitude,longitude,hazard_type,severity,severity_score,confidence,"
                    "first_detected,last_detected,detection_count,status,source,heading,speed,verified,route_id)"
                    " VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?,?)",
                    (lat, lon, hazard_type, severity, severity_score, confidence, ts, ts, STATUS_ACTIVE,
                     source, heading, speed, int(verified), route_id))
                hid, created = int(cur.lastrowid), True
                log.info("Created hazard #%s %s at %.6f,%.6f", hid, hazard_type, lat, lon)
            self._conn.execute(
                "INSERT INTO detections(hazard_id,route_id,ts,hazard_type,confidence,bbox,latitude,longitude,"
                "vehicle_lat,vehicle_lon,distance_m,speed,heading,imu_impact) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (hid, route_id, ts, hazard_type, confidence, json.dumps(bbox) if bbox else None, lat, lon,
                 vehicle_pos[0] if vehicle_pos else None, vehicle_pos[1] if vehicle_pos else None,
                 distance_m, speed, heading, impact))
            self._conn.commit()
            return self.get_hazard(hid), created  # type: ignore[return-value]

    def get_hazard(self, hazard_id: int) -> Optional[Hazard]:
        rows = self._query("SELECT * FROM hazards WHERE id=?", (hazard_id,))
        return Hazard.from_row(rows[0]) if rows else None

    def get_hazards(self, status: Optional[str] = None, hazard_type: Optional[str] = None,
                    limit: int = 20000) -> List[Hazard]:
        sql, params = "SELECT * FROM hazards WHERE 1=1", []
        if status:
            sql += " AND status=?"
            params.append(status)
        if hazard_type:
            sql += " AND hazard_type=?"
            params.append(hazard_type)
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)
        return [Hazard.from_row(r) for r in self._query(sql, tuple(params))]

    def set_hazard_status(self, hazard_id: int, status: str) -> bool:
        status = status.upper()
        if status not in STATUSES:
            raise ValueError(f"invalid status {status!r}")
        return self._exec("UPDATE hazards SET status=? WHERE id=?", (status, hazard_id)).rowcount > 0

    def set_hazard_image(self, hazard_id: int, image_path: str) -> None:
        self._exec("UPDATE hazards SET image_path=? WHERE id=?", (image_path, hazard_id))

    def mark_impact_verified(self, lat: float, lon: float, radius_m: float, within_s: float) -> List[int]:
        """IMU impact: verify hazards created/seen near here very recently."""
        a, b, c, d = bbox_around(lat, lon, radius_m)
        now = time.time()
        out = []
        with self._lock:
            for r in self._conn.execute("SELECT id, latitude, longitude, last_detected FROM hazards"
                                        " WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?",
                                        (a, b, c, d)).fetchall():
                t = iso_to_epoch(r["last_detected"]) or 0
                if now - t <= within_s and haversine_m(lat, lon, r["latitude"], r["longitude"]) <= radius_m:
                    self._conn.execute("UPDATE hazards SET verified=1 WHERE id=?", (r["id"],))
                    out.append(r["id"])
            self._conn.commit()
        return out

    def delete_all_hazards(self) -> None:
        with self._lock:
            for t in ("detections", "hazards", "gps_tracks", "routes"):
                self._conn.execute(f"DELETE FROM {t}")
            self._conn.commit()

    # ------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self._query("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key: str, value: Any) -> None:
        self._exec("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(value)))

    def all_settings(self) -> Dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self._query("SELECT key,value FROM settings")}

    # ----------------------------------------------------------- statistics
    def statistics(self) -> Dict[str, Any]:
        q = lambda s: self._query(s)[0][0]  # noqa: E731
        by_status = {r[0]: r[1] for r in self._query("SELECT status, COUNT(*) FROM hazards GROUP BY status")}
        by_type = {r[0]: r[1] for r in self._query("SELECT hazard_type, COUNT(*) FROM hazards GROUP BY hazard_type")}
        by_sev = {r[0]: r[1] for r in self._query("SELECT severity, COUNT(*) FROM hazards GROUP BY severity")}
        return {
            "hazards_total": q("SELECT COUNT(*) FROM hazards"),
            "by_status": by_status, "by_type": by_type, "by_severity": by_sev,
            "detections_total": q("SELECT COUNT(*) FROM detections"),
            "routes_total": q("SELECT COUNT(*) FROM routes"),
            "track_points": q("SELECT COUNT(*) FROM gps_tracks"),
            "db_size_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }
