"""Pre-download map tiles for OFFLINE use (saved to data/tiles/{z}/{x}/{y}.png).

    python scripts/download_tiles.py --preset ncr --max-zoom 12
    python scripts/download_tiles.py --bbox 28.40 76.95 28.90 77.45 --min-zoom 13 --max-zoom 15 \
        --url https://YOUR-TILE-SERVER/{z}/{x}/{y}.png

IMPORTANT: tile.openstreetmap.org forbids bulk downloading (see
https://operations.osmfoundation.org/policies/tiles/).  The default URL is
therefore capped to a small overview (<= 2000 tiles).  For street-level
offline maps of the whole of Delhi NCR use a tile provider/licence that allows
it, or self-host (OpenMapTiles / Protomaps) and pass --url.
"""
import argparse
import math
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESETS = {"ncr": (27.75, 76.75, 29.15, 78.05), "delhi": (28.40, 76.83, 28.89, 77.35)}
OSM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_CAP = 2000


def tile_xy(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", choices=PRESETS)
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"))
    ap.add_argument("--min-zoom", type=int, default=8)
    ap.add_argument("--max-zoom", type=int, default=12)
    ap.add_argument("--url", default=OSM)
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    ap.add_argument("--out", default=str(ROOT / "data" / "tiles"))
    a = ap.parse_args()
    s, w, n, e = a.bbox or PRESETS[a.preset or "ncr"]
    todo = []
    for z in range(a.min_zoom, a.max_zoom + 1):
        x0, y1 = tile_xy(s, w, z)
        x1, y0 = tile_xy(n, e, z)
        todo += [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
    print(f"{len(todo)} tiles to fetch")
    if a.url == OSM and len(todo) > OSM_CAP:
        sys.exit(f"Refusing: {len(todo)} tiles from tile.openstreetmap.org exceeds the {OSM_CAP}-tile fair-use cap. "
                 "Lower --max-zoom or supply --url of a server that permits bulk download.")
    got = 0
    for z, x, y in todo:
        f = Path(a.out) / str(z) / str(x) / f"{y}.png"
        if f.exists():
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            req = urllib.request.Request(a.url.format(z=z, x=x, y=y),
                                         headers={"User-Agent": "SmartRoad-offline-tiles/1.0 (personal research)"})
            f.write_bytes(urllib.request.urlopen(req, timeout=15).read())
            got += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {z}/{x}/{y}: {exc}")
        time.sleep(a.delay)
    print(f"done, downloaded {got} new tiles into {a.out}")


if __name__ == "__main__":
    main()
