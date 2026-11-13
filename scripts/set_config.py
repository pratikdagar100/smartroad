"""Set one key in config.yaml without losing comments:
    python scripts/set_config.py gps.device /dev/ttyACM0
"""
import re
import sys
from pathlib import Path

def main(dotted, value, path="config.yaml"):
    sec, key = dotted.split(".")
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines()
    in_sec, done = False, False
    for i, ln in enumerate(lines):
        if re.match(r"^\S", ln):
            in_sec = ln.startswith(sec + ":")
        elif in_sec and re.match(rf"^\s+{re.escape(key)}\s*:", ln):
            indent = re.match(r"^(\s+)", ln).group(1)
            comment = re.search(r"\s+#.*$", ln)
            lines[i] = f"{indent}{key}: {value}" + (comment.group(0) if comment else "")
            done = True
            break
    if not done:
        lines += ["", f"{sec}:", f"  {key}: {value}"]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{dotted} = {value}")

if __name__ == "__main__":
    main(*sys.argv[1:3])
