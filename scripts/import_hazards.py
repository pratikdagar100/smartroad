"""Import real pothole records into the local SmartRoad database.

    python scripts/import_hazards.py potholes.csv --source "MCD-complaints-2026"
    python scripts/import_hazards.py potholes.geojson --db data/smartroad.db

CSV columns (header row, case-insensitive): latitude,longitude  [lat/lon/lng also accepted]
optional: type, severity (LOW|MEDIUM|HIGH), confidence (0-1), date (ISO 8601), status.
GeoJSON: Point features; the same optional fields are read from ``properties``.
Duplicates within the merge radius are merged, so re-running is safe.
"""
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import load_config  # noqa: E402
from database.db import Database  # noqa: E402
from mapping.geo import lat_lon_valid  # noqa: E402

KEYS_LAT, KEYS_LON = ("latitude", "lat", "y"), ("longitude", "lon", "lng", "long", "x")
SEV_SCORE = {"LOW": 0.2, "MEDIUM": 0.5, "HIGH": 0.8}


def pick(d, keys):
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        if low.get(k) not in (None, ""):
            return low[k]
    return None


def rows(path: Path):
    if path.suffix.lower() in (".geojson", ".json"):
        for f in json.loads(path.read_text(encoding="utf-8")).get("features", []):
            if f.get("geometry", {}).get("type") == "Point":
                lon, lat = f["geometry"]["coordinates"][:2]
                yield {**(f.get("properties") or {}), "latitude": lat, "longitude": lon}
    else:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            yield from csv.DictReader(fh)


def iso(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if dt.tzinfo else \
            dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--db", help="database path (default: config database.path)")
    ap.add_argument("--source", default="import", help="label stored in hazards.source")
    a = ap.parse_args()
    cfg = load_config()
    db = Database(a.db or cfg.resolve(cfg.database.path))
    made = merged = skipped = 0
    for r in rows(Path(a.file)):
        try:
            lat, lon = float(pick(r, KEYS_LAT)), float(pick(r, KEYS_LON))
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not lat_lon_valid(lat, lon):
            skipped += 1
            continue
        sev = str(pick(r, ("severity",)) or "MEDIUM").upper()
        sev = sev if sev in SEV_SCORE else "MEDIUM"
        h, created = db.record_hazard_event(
            lat=lat, lon=lon, hazard_type=str(pick(r, ("type", "hazard_type")) or "pothole").lower(),
            confidence=float(pick(r, ("confidence",)) or 0.7), severity=sev, severity_score=SEV_SCORE[sev],
            merge_radius_m=cfg.hazards.merge_radius_m, source=a.source, ts=iso(pick(r, ("date", "timestamp"))))
        st = str(pick(r, ("status",)) or "").upper()
        if st in ("FIXED", "UNKNOWN"):
            db.set_hazard_status(h.id, st)
        made += created
        merged += (not created)
    db.close()
    print(f"imported: {made} new, {merged} merged, {skipped} skipped")


if __name__ == "__main__":
    main()
