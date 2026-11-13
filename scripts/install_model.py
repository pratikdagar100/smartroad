"""Download and verify the default SmartRoad pothole model.

    python scripts/install_model.py            # default catalogue model -> models/pothole.onnx
    python scripts/install_model.py --list
    python scripts/install_model.py --id yolov8n-pothole-seg --force
Exit code 0 on success (or already installed), 1 on failure.  Never fatal for the installers.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from detection.model_manager import install_entry, load_catalog  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    models = ROOT / "models"
    cat = load_catalog(models)
    if a.list:
        for e in cat:
            print(f"{e['id']:<24} {e['size_mb']:>4} MB  {e['name']}")
        return 0
    entry = next((e for e in cat if e["id"] == a.id), None) if a.id else (cat[0] if cat else None)
    if not entry:
        print("No such model in models/catalog.json")
        return 1
    last = [-1]

    def prog(p):
        pct = int(p * 100)
        if pct // 10 != last[0] // 10:
            print(f"  downloading {entry['file']} ... {pct}%", flush=True)
            last[0] = pct
    try:
        path = install_entry(entry, models, prog, force=a.force)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARNING] model not installed: {exc}\n          The app still runs; install later from Settings > AI model.")
        return 1
    print(f"[OK] AI model ready: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
