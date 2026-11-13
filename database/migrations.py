"""Versioned SQLite schema.  ``PRAGMA user_version`` records the applied version."""
from __future__ import annotations

import sqlite3
from typing import Callable, List

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS vehicles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS routes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id  INTEGER REFERENCES vehicles(id),
    name        TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    distance_m  REAL NOT NULL DEFAULT 0,
    point_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS hazards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    latitude        REAL NOT NULL,
    longitude       REAL NOT NULL,
    hazard_type     TEXT NOT NULL DEFAULT 'pothole',
    severity        TEXT NOT NULL DEFAULT 'LOW',
    severity_score  REAL NOT NULL DEFAULT 0,
    confidence      REAL NOT NULL DEFAULT 0,
    image_path      TEXT,
    first_detected  TEXT NOT NULL,
    last_detected   TEXT NOT NULL,
    detection_count INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE','FIXED','UNKNOWN')),
    source          TEXT NOT NULL DEFAULT 'camera',
    heading         REAL,
    speed           REAL,
    verified        INTEGER NOT NULL DEFAULT 0,
    route_id        INTEGER REFERENCES routes(id)
);
CREATE INDEX IF NOT EXISTS idx_hazards_pos ON hazards(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_hazards_status ON hazards(status);

CREATE TABLE IF NOT EXISTS detections (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    hazard_id    INTEGER REFERENCES hazards(id) ON DELETE SET NULL,
    route_id     INTEGER REFERENCES routes(id),
    ts           TEXT NOT NULL,
    hazard_type  TEXT NOT NULL,
    confidence   REAL NOT NULL,
    bbox         TEXT,
    latitude     REAL NOT NULL,
    longitude    REAL NOT NULL,
    vehicle_lat  REAL,
    vehicle_lon  REAL,
    distance_m   REAL,
    speed        REAL,
    heading      REAL,
    imu_impact   REAL
);
CREATE INDEX IF NOT EXISTS idx_detections_hazard ON detections(hazard_id);

CREATE TABLE IF NOT EXISTS gps_tracks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id  INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    ts        TEXT NOT NULL,
    latitude  REAL NOT NULL,
    longitude REAL NOT NULL,
    altitude  REAL,
    speed     REAL,
    heading   REAL,
    accuracy  REAL
);
CREATE INDEX IF NOT EXISTS idx_tracks_route ON gps_tracks(route_id, id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

MIGRATIONS: List[Callable[[sqlite3.Connection], None]] = [
    lambda c: c.executescript(SCHEMA_V1),
]


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Apply any pending migrations. Idempotent."""
    v = current_version(conn)
    for i in range(v, len(MIGRATIONS)):
        MIGRATIONS[i](conn)
        conn.execute(f"PRAGMA user_version = {i + 1}")
        conn.commit()
    return current_version(conn)
