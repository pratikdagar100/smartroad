# Troubleshooting

First look at **HEALTH** in the UI, `logs/smartroad.log`, and `python -m app.hardware_test`.

| Symptom | Cause / fix |
|---|---|
| `CAMERA ERROR` | Wrong `camera.backend`/`device`; camera in use by another program; CSI cable; user not in `video` group (re-login). Test `rpicam-hello` or `ls /dev/video*`. App retries every 3 s. |
| `AI MODEL NOT INSTALLED` | No file at `model.path`. See models/README.md. Not an error for testing the map/GPS. |
| AI error `output channels … classes` | `model.classes` count doesn't match the model; fix the list or set `output_format`. |
| `GPS UNAVAILABLE` | Port can't open: wrong `gps.device`, no `dialout` group, UART not enabled, gpsd holding the port. |
| `GPS SIGNAL LOST` / waiting for fix | Needs sky view, < `gps.min_satellites` (4) or no update for `gps.max_age_s`. No hazards are recorded without a fix (by design). |
| No potholes saved though boxes show | Box must persist `events.min_stable_frames`, pass `events.min_confidence`, GPS fix valid, not within cooldown/min-distance of a recent event. Health page lists rejected-candidate reasons. |
| Duplicate potholes | Increase `hazards.merge_radius_m` (3–6 m); GPS error and the fixed distance estimate shift positions. |
| Warning appears for a hazard just detected | Intentional suppression for `proximity.new_hazard_quiet_s`. |
| Map grey / "map tiles offline" | No internet and no `data/tiles`; hazards/route/vehicle still work. Add tiles with `scripts/download_tiles.py` (respect tile-server policy). |
| UI blank on Pi after boot | Desktop autologin off; check `~/.config/autostart/smartroad-kiosk.desktop`, `systemctl status smartroad`, run `scripts/smartroad-kiosk.sh` manually. |
| Service fails: status 217/USER or 216/GROUP | Edit `/etc/systemd/system/smartroad.service` (`@USER@` etc. filled by installer; re-run `./install_pi.sh`). |
| `Address already in use` | Another instance: `./stop_smartroad.sh`, or change `app.port`. |
| Windows: `Python not found` | Install Python 3.9+ with "Add to PATH"; or use the `py` launcher. |
| Windows: webcam not found | `run_windows.bat --camera webcam --device 1`, or `--camera video --source file.mp4`, or `--demo`. |
| High CPU / temperature | `model.frame_skip: 2`, smaller `input_size`, `process_width: 480`, cooling. |
| 32-bit OS: no onnxruntime | Use 64-bit Raspberry Pi OS or a `.tflite` model. |
| Reset the data | Stop the app, delete `data/smartroad.db*` (hazard images are in `data/hazards/`). |
