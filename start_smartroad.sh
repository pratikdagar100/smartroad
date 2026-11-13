#!/usr/bin/env bash
# Start SmartRoad (backend via systemd if installed, else directly in background).
set -u
cd "$(dirname "$(readlink -f "$0")")"
if systemctl list-unit-files smartroad.service >/dev/null 2>&1 && systemctl cat smartroad.service >/dev/null 2>&1; then
  sudo systemctl start smartroad.service && echo "SmartRoad service started."
else
  [ -x .venv/bin/python ] || { echo "Run ./install_pi.sh first."; exit 1; }
  mkdir -p logs
  if [ -f logs/smartroad.pid ] && kill -0 "$(cat logs/smartroad.pid)" 2>/dev/null; then echo "Already running."; exit 0; fi
  nohup .venv/bin/python -m app.main "$@" >> logs/stdout.log 2>&1 &
  echo $! > logs/smartroad.pid
  echo "SmartRoad started (pid $(cat logs/smartroad.pid))."
fi
