#!/usr/bin/env bash
# Show SmartRoad service state, API health and recent log lines.
cd "$(dirname "$(readlink -f "$0")")"
PORT=$(grep -E '^\s*port:' config.yaml 2>/dev/null | head -1 | awk '{print $2}'); PORT=${PORT:-8000}
echo "== service =="; systemctl is-active smartroad.service 2>/dev/null || echo "(not installed / not running)"
echo "== API =="
if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/api/status" 2>/dev/null; then echo; else echo "API not reachable on port ${PORT}"; fi
echo "== last log lines =="; tail -n 15 logs/smartroad.log 2>/dev/null || echo "(no log yet)"
