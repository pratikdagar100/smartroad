#!/usr/bin/env bash
# Open the SmartRoad UI full-screen in Chromium once the backend answers.
# Started by the desktop autostart entry created by install_pi.sh.
DIR="$(dirname "$(dirname "$(readlink -f "$0")")")"
PORT=$(grep -E '^\s*port:' "$DIR/config.yaml" 2>/dev/null | head -1 | awk '{print $2}'); PORT=${PORT:-8000}
URL="http://127.0.0.1:${PORT}/"
for _ in $(seq 1 120); do                      # wait up to ~2 min for the backend
  curl -fsS --max-time 2 "${URL}api/status" >/dev/null 2>&1 && break
  sleep 1
done
BROWSER=$(command -v chromium-browser || command -v chromium || true)
[ -n "$BROWSER" ] || { echo "Chromium not installed (sudo apt install chromium-browser)"; exit 1; }
# no screen blanking while driving
xset s off -dpms 2>/dev/null || true
exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars --incognito --no-first-run \
  --password-store=basic --disable-session-crashed-bubble --disable-translate \
  --overscroll-history-navigation=0 --check-for-update-interval=31536000 \
  --user-data-dir="$HOME/.cache/smartroad-kiosk" "$URL"
