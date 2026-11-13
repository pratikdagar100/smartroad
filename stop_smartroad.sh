#!/usr/bin/env bash
# Stop SmartRoad (service or background process) and the kiosk browser.
cd "$(dirname "$(readlink -f "$0")")"
if systemctl cat smartroad.service >/dev/null 2>&1; then sudo systemctl stop smartroad.service; fi
if [ -f logs/smartroad.pid ]; then kill "$(cat logs/smartroad.pid)" 2>/dev/null; rm -f logs/smartroad.pid; fi
pkill -f "smartroad-kiosk" 2>/dev/null || true
echo "SmartRoad stopped."
