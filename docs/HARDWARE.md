# Hardware & wiring

## Bill of materials
Raspberry Pi 4 (4 GB+) or Pi 5 · Pi Camera Module 3 / any CSI camera **or** UVC USB webcam · NMEA GPS (USB puck such as
u-blox 7/8, or UART module such as NEO-6M/M8N) · 5-inch to 10-inch HDMI/DSI display · 12 V→5 V (3 A Pi 4, 5 A Pi 5)
automotive converter · optional MPU6050 · heatsink/fan.

## Camera
* **CSI:** power off, connect to CAM0/CAM1 (Pi 5 uses the small 22-pin connector; use the right ribbon), `camera.backend: picamera2`.
  Test: `libcamera-hello` / `rpicam-hello`.
* **USB:** any UVC webcam, `camera.backend: opencv`, `camera.device: 0`. Prefer USB 3 ports (blue).
* Mount behind the windscreen, level, horizon ≈ 40 % down the frame, hood just visible at the bottom.

## GPS
**USB GPS** — plug in; appears as `/dev/ttyUSB0` or `/dev/ttyACM0`; set `gps.device`. Check: `cat /dev/ttyACM0` shows `$GPGGA…`.

**UART GPS** (3.3 V logic only!)
| GPS | Pi header |
|---|---|
| VCC | 3.3 V or 5 V per module spec (pin 1 / 2) |
| GND | GND (pin 6) |
| TX | GPIO15 / RXD (pin 10) |
| RX | GPIO14 / TXD (pin 8) |

Enable: `sudo raspi-config nonint do_serial_hw 0` (hardware UART on) and `do_serial_cons 1` (login console off),
reboot, set `gps.device: /dev/serial0`. Pi 5: the header UART is `/dev/ttyAMA0` (alias `serial0` may exist).
Tools: `sudo apt install gpsd-clients` → `cgps` (stop gpsd if it grabs the port: `sudo systemctl stop gpsd.socket gpsd`).

## Optional MPU6050 (impact verification)
| MPU6050 | Pi |
|---|---|
| VCC | 3.3 V (pin 1) |
| GND | GND (pin 9) |
| SDA | GPIO2 / SDA (pin 3) |
| SCL | GPIO3 / SCL (pin 5) |

`sudo raspi-config nonint do_i2c 0`, `sudo apt install i2c-tools`, `i2cdetect -y 1` should show `68`. Set `imu.enabled: true`.
Mount rigidly to the car body. Without it everything still works.

## Power
Use a quality converter; under-voltage (lightning-bolt icon, `vcgencmd get_throttled` ≠ 0x0) causes camera/USB dropouts.
Connect to an ignition-switched supply, or add a safe-shutdown module so the SD card isn't cut mid-write
(SQLite WAL is crash-safe, but be kind to the card).

## Extension points
LiDAR / stereo / depth → subclass `mapping.geolocate.DistanceEstimator` · AI accelerator → new `BaseDetector` ·
4G modem → cloud sync module reading `SMARTROAD_CLOUD_URL` / `SMARTROAD_CLOUD_TOKEN`.
