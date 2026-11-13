"""Generate data/demo_route.json and data/demo_hazards.json (run from project root).

The route is an ~1 km drive near Connaught Place, New Delhi, with a few bends.
The simulator drives it out and back, so every pothole is met twice.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mapping.geo import bearing_deg, destination_point, haversine_m, resample_path  # noqa: E402

WAYPOINTS = [(28.6139, 77.2090), (28.6155, 77.2112), (28.6170, 77.2135), (28.6178, 77.2165),
             (28.6176, 77.2195), (28.6168, 77.2222), (28.6160, 77.2250)]

# (fraction along route, lateral offset in metres (+ right), known at start?, width_m)
POTHOLES = [(0.14, 1.2, False, 1.2), (0.30, -1.5, False, 1.4), (0.46, 0.8, True, 1.2),
            (0.62, -1.0, False, 1.3), (0.78, 1.4, True, 1.2), (0.90, -0.6, False, 1.5)]


def main():
    pts = resample_path(WAYPOINTS, 10.0)
    route = [{"latitude": round(a, 6), "longitude": round(b, 6)} for a, b in pts]
    total = sum(haversine_m(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1))
    hazards = []
    for frac, lat_off, known, width in POTHOLES:
        target = total * frac
        acc = 0.0
        for i in range(len(pts) - 1):
            seg = haversine_m(*pts[i], *pts[i + 1])
            if acc + seg >= target:
                f = (target - acc) / seg
                lat = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f
                lon = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f
                brg = bearing_deg(*pts[i], *pts[i + 1])
                lat, lon = destination_point(lat, lon, (brg + 90) % 360, lat_off)
                hazards.append({"latitude": round(lat, 6), "longitude": round(lon, 6), "type": "pothole",
                                "width_m": width, "known": known})
                break
            acc += seg
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "demo_route.json").write_text(json.dumps(route, indent=1), encoding="utf-8")
    (ROOT / "data" / "demo_hazards.json").write_text(json.dumps(hazards, indent=1), encoding="utf-8")
    print(f"route: {len(route)} points, {total:.0f} m; hazards: {len(hazards)}")


if __name__ == "__main__":
    main()
