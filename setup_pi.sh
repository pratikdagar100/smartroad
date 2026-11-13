#!/usr/bin/env bash
# ONE-CLICK Raspberry Pi setup: accepts every prompt of install_pi.sh
# (packages, venv, dependencies, AI model, database, systemd service, kiosk autostart, self-test).
cd "$(dirname "$(readlink -f "$0")")"
chmod +x install_pi.sh
exec ./install_pi.sh --yes "$@"
