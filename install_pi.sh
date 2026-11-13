#!/usr/bin/env bash
# SmartRoad installer for Raspberry Pi OS (Pi 4 / Pi 5).  Safe to run repeatedly.
#   ./install_pi.sh            interactive (asks before system-wide changes)
#   ./install_pi.sh --yes      accept all prompts (non-interactive)
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"
DIR="$(pwd)"
ASSUME_YES=0; [ "${1:-}" = "--yes" ] && ASSUME_YES=1

c_ok=$'\e[32m'; c_warn=$'\e[33m'; c_err=$'\e[31m'; c_off=$'\e[0m'
ok()   { echo "${c_ok}[OK]${c_off} $*"; }
warn() { echo "${c_warn}[WARNING]${c_off} $*"; }
die()  { echo "${c_err}[ERROR]${c_off} $*"; exit 1; }
ask()  { # ask "question"  -> 0 for yes
  [ "$ASSUME_YES" = 1 ] && return 0
  read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]
}

# ---------------------------------------------------------------- 1. platform
[ "$(uname -s)" = "Linux" ] || die "This installer is for Raspberry Pi OS (Linux). On Windows use run_windows.bat."
[ "$(id -u)" -ne 0 ] || die "Run as your normal user (not root / sudo). The script calls sudo only where needed."
ARCH="$(uname -m)"
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
OS_NAME="$(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
OS_CODENAME="$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-}")"
echo "Machine : ${MODEL:-unknown}  ($ARCH)"
echo "OS      : $OS_NAME"
if [[ "$MODEL" != *"Raspberry Pi"* ]]; then
  warn "Not detected as a Raspberry Pi."; ask "Continue anyway?" || exit 1
elif [[ "$MODEL" == *"Raspberry Pi 5"* ]]; then ok "Raspberry Pi 5 detected"
elif [[ "$MODEL" == *"Raspberry Pi 4"* ]]; then ok "Raspberry Pi 4 detected"
else warn "Untested Raspberry Pi model ($MODEL) - Pi 4 and Pi 5 are supported"; fi
case "$ARCH" in
  aarch64) ok "64-bit OS" ;;
  armv7l|armv6l) warn "32-bit OS: onnxruntime has no wheel, so the ONNX model cannot run. Use 64-bit Raspberry Pi OS (recommended) or a .tflite model." ;;
  *) warn "Unusual architecture $ARCH" ;;
esac
command -v python3 >/dev/null || die "python3 not found"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' || die "Python 3.9+ required (found $(python3 -V))"
ok "$(python3 -V)"

# ------------------------------------------------------------ 2. OS packages
need=()
pkg_missing() { ! dpkg -s "$1" >/dev/null 2>&1; }
avail() { apt-cache show "$1" >/dev/null 2>&1; }
for p in python3-venv python3-pip python3-numpy python3-opencv python3-serial python3-psutil i2c-tools curl; do
  pkg_missing "$p" && avail "$p" && need+=("$p")
done
avail python3-picamera2 && pkg_missing python3-picamera2 && need+=(python3-picamera2)
if ! command -v chromium-browser >/dev/null && ! command -v chromium >/dev/null; then
  if avail chromium-browser; then need+=(chromium-browser); elif avail chromium; then need+=(chromium); fi
fi
if [ ${#need[@]} -gt 0 ]; then
  echo "Missing system packages: ${need[*]}"
  if ask "Install them with apt (no upgrade of existing packages)?"; then
    sudo apt-get update && sudo apt-get install -y --no-install-recommends "${need[@]}" || warn "apt install reported errors"
  else warn "Skipped apt packages - camera/UI may not work"; fi
else ok "System packages present"; fi

# ----------------------------------------------------------- 3. virtualenv
# --system-site-packages: reuse apt's numpy/opencv/picamera2 (never overwritten by pip)
if [ ! -x .venv/bin/python ]; then
  python3 -m venv --system-site-packages .venv || die "could not create virtualenv"
  ok "Created .venv"
else ok ".venv exists"; fi
.venv/bin/python -m pip install --quiet --upgrade pip 2>/dev/null || warn "pip upgrade skipped"
if ! .venv/bin/python -m pip install --quiet -r requirements-pi.txt; then
  warn "Bulk pip install failed - retrying package by package"
  while read -r line; do
    pkg="${line%%#*}"; pkg="$(echo "$pkg" | xargs)"; [ -z "$pkg" ] && continue
    .venv/bin/python -m pip install --quiet "$pkg" || warn "could not install $pkg"
  done < requirements-pi.txt
fi
.venv/bin/python - <<'PY' || warn "Some Python modules are missing - see messages above"
import importlib
for m in ("fastapi", "uvicorn", "yaml", "serial", "psutil", "numpy", "cv2"):
    importlib.import_module(m)
try:
    import onnxruntime  # noqa
except ImportError:
    print("  note: onnxruntime unavailable (AI model disabled; app still runs)")
PY
ok "Python dependencies installed"

# --------------------------------------------------------- 4. folders / DB
mkdir -p data/hazards data/tiles logs models
[ -f data/demo_route.json ] || .venv/bin/python scripts/make_demo_data.py >/dev/null
.venv/bin/python -m app.main --init-db >/dev/null && ok "SQLite database initialised" || warn "database init failed"
chmod +x *.sh scripts/*.sh
echo "Installing the AI pothole model ..."
.venv/bin/python scripts/install_model.py || warn "Model not installed - install later from the UI (Settings > AI model) or: .venv/bin/python scripts/install_model.py"

# ------------------------------------------------------- 5. user groups / HW
GROUPS_OK=()
for g in video dialout i2c gpio; do getent group "$g" >/dev/null && GROUPS_OK+=("$g"); done
missing_groups=()
for g in video dialout i2c; do getent group "$g" >/dev/null && ! id -nG "$USER" | grep -qw "$g" && missing_groups+=("$g"); done
if [ ${#missing_groups[@]} -gt 0 ]; then
  if ask "Add $USER to groups: ${missing_groups[*]} (camera / serial GPS / I2C access)?"; then
    for g in "${missing_groups[@]}"; do sudo usermod -aG "$g" "$USER"; done
    warn "Group changes take effect after reboot/re-login"
  fi
fi

# GPS auto-detect
GPS_DEV=""
for d in /dev/ttyUSB0 /dev/ttyACM0 /dev/serial0 /dev/ttyAMA0; do [ -e "$d" ] && { GPS_DEV="$d"; break; }; done
if [ -n "$GPS_DEV" ]; then
  ok "Serial device found: $GPS_DEV"
  if ! grep -Eq "^\s+device:\s*$GPS_DEV" config.yaml && ask "Use $GPS_DEV as the GPS device in config.yaml?"; then
    .venv/bin/python scripts/set_config.py gps.device "$GPS_DEV"
  fi
else warn "No GPS serial device found yet (USB GPS: plug in; UART GPS: see docs/HARDWARE.md). Edit gps.device in config.yaml later."; fi
if [ "$GPS_DEV" = "/dev/serial0" ] || [ "$GPS_DEV" = "/dev/ttyAMA0" ]; then
  if command -v raspi-config >/dev/null && ask "Enable the hardware UART (disable serial login console) via raspi-config?"; then
    sudo raspi-config nonint do_serial_hw 0 && sudo raspi-config nonint do_serial_cons 1 && warn "Reboot needed for UART changes"
  fi
fi
if command -v raspi-config >/dev/null && [ "$(raspi-config nonint get_i2c 2>/dev/null)" = "1" ]; then
  ask "Enable I2C (needed only for the optional MPU6050 IMU)?" && sudo raspi-config nonint do_i2c 0
fi

# camera check
if .venv/bin/python - <<'PY' 2>/dev/null
from picamera2 import Picamera2
import sys
sys.exit(0 if Picamera2.global_camera_info() else 1)
PY
then ok "Raspberry Pi camera detected (Picamera2)"
elif ls /dev/video* >/dev/null 2>&1; then ok "V4L2 video device(s): $(ls /dev/video* | tr '\n' ' ')"
else warn "No camera detected - connect a Pi camera or USB webcam (config.yaml camera.backend)"; fi

# ------------------------------------------------------------ 6. systemd
if ask "Install the systemd service so SmartRoad starts at boot?"; then
  GROUP_LIST="${GROUPS_OK[*]}"
  tmp="$(mktemp)"
  sed -e "s|@USER@|$USER|g" -e "s|@DIR@|$DIR|g" -e "s|@GROUPS@|$GROUP_LIST|g" smartroad.service > "$tmp"
  sudo install -m 644 "$tmp" /etc/systemd/system/smartroad.service && rm -f "$tmp"
  sudo systemctl daemon-reload && sudo systemctl enable smartroad.service && ok "smartroad.service enabled"
  START_SERVICE=1
else START_SERVICE=0; warn "Service not installed - start manually with ./start_smartroad.sh"; fi

# ------------------------------------------------- 7. kiosk (display) autostart
if ask "Open SmartRoad full-screen automatically after boot (Chromium kiosk)?"; then
  mkdir -p "$HOME/.config/autostart"
  cat > "$HOME/.config/autostart/smartroad-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SmartRoad Kiosk
Exec=$DIR/scripts/smartroad-kiosk.sh
X-GNOME-Autostart-enabled=true
EOF
  # Raspberry Pi OS Bookworm: labwc (Wayland) and wayfire read their own autostart files
  if command -v labwc >/dev/null || [ -d "$HOME/.config/labwc" ]; then
    mkdir -p "$HOME/.config/labwc"
    grep -qs "smartroad-kiosk" "$HOME/.config/labwc/autostart" || echo "$DIR/scripts/smartroad-kiosk.sh &" >> "$HOME/.config/labwc/autostart"
  fi
  if [ -f "$HOME/.config/wayfire.ini" ] && ! grep -q "smartroad-kiosk" "$HOME/.config/wayfire.ini"; then
    if grep -q '^\[autostart\]' "$HOME/.config/wayfire.ini"; then
      sed -i "/^\[autostart\]/a smartroad = $DIR/scripts/smartroad-kiosk.sh" "$HOME/.config/wayfire.ini"
    else printf '\n[autostart]\nsmartroad = %s/scripts/smartroad-kiosk.sh\n' "$DIR" >> "$HOME/.config/wayfire.ini"; fi
  fi
  command -v raspi-config >/dev/null && ask "Disable screen blanking?" && sudo raspi-config nonint do_blanking 1
  ok "Kiosk autostart configured (also make sure the desktop auto-logs in: raspi-config -> System -> Boot -> Desktop Autologin)"
fi

# ------------------------------------------------------------- 8. self test
echo; echo "Running self-test (python -m app.hardware_test) ..."
.venv/bin/python -m app.hardware_test --gps-seconds 4 || warn "Self-test reported failures - see above"
[ -f models/pothole.onnx ] || warn "No AI model yet: copy your trained model to models/pothole.onnx (see models/README.md). Until then the UI shows 'AI MODEL NOT INSTALLED'."

if [ "${START_SERVICE:-0}" = 1 ] && ask "Start SmartRoad now?"; then sudo systemctl restart smartroad.service; fi
echo; ok "Installation finished. Reboot to test autostart:  sudo reboot"
