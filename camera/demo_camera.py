"""Synthetic dashcam scene for ``--demo`` mode (no hardware needed).

Renders a perspective road and draws every demo pothole that is ahead of the
simulated vehicle.  The projection is the exact inverse of
``mapping.geolocate.FlatRoadEstimator`` so the demo's bbox-based geolocation
lands on the true pothole position.  Ground-truth boxes are attached to the
frame (``meta['truth']``) for the simulated detector.
"""
from __future__ import annotations

import math
import time
from typing import List, Tuple

import cv2
import numpy as np

from mapping.geo import angle_diff_deg, bearing_deg, haversine_m
from .base_camera import BaseCamera

W, H = 640, 360


class DemoCamera(BaseCamera):
    name = "demo"

    def __init__(self, simulator, potholes: List[dict], geo_cfg, fps: int = 20, **kw):
        super().__init__(**kw)
        self.sim, self.potholes, self.g, self.fps = simulator, potholes, geo_cfg, fps
        rng = np.random.default_rng(3)
        self._noise = rng.normal(0, 7, (H, W, 1)).astype(np.float32)
        self._horizon = int(H * self.g.horizon_ratio)
        self._tan_half = math.tan(math.radians(self.g.hfov_deg / 2))
        self._last = 0.0
        self._odo = 0.0

    def _open(self) -> None:
        pass

    def _project(self, vlat, vlon, vhdg, p) -> Tuple[bool, dict]:
        d = haversine_m(vlat, vlon, p["latitude"], p["longitude"])
        brg = bearing_deg(vlat, vlon, p["latitude"], p["longitude"])
        rel = (brg - vhdg + 180) % 360 - 180
        if abs(rel) > self.g.hfov_deg / 2 * 0.95 or d < 4.5 or d > self.g.max_distance_m - 3:
            return False, {}
        yb = self.g.horizon_ratio + self.g.ground_k / d
        if yb >= 1.0:
            return False, {}
        xn = 0.5 + 0.5 * math.tan(math.radians(rel)) / self._tan_half
        f_px = (W / 2) / self._tan_half
        w_px = p.get("width_m", 1.2) * f_px / d
        h_px = max(3.0, w_px * 0.38)
        x1, x2 = xn * W - w_px / 2, xn * W + w_px / 2
        y2 = yb * H
        y1 = y2 - h_px
        return True, {"bbox": (x1, y1, x2, y2), "d": d, "conf": 0.78 + 0.18 * max(0.0, 1 - d / 50)}

    def _render(self, vlat, vlon, vhdg, speed) -> Tuple[np.ndarray, list]:
        img = np.empty((H, W, 3), np.uint8)
        hz = self._horizon
        # sky gradient
        sky = np.linspace((235, 190, 140), (250, 225, 200), hz, dtype=np.float32)  # BGR
        img[:hz] = sky[:, None, :].astype(np.uint8)
        img[hz:] = (70, 90, 80)  # roadside verge
        # distant skyline
        for i, (x, w_, h_) in enumerate([(30, 70, 28), (120, 50, 40), (200, 90, 22), (330, 60, 36),
                                         (420, 80, 26), (520, 70, 44), (590, 60, 30)]):
            cv2.rectangle(img, (x, hz - h_), (x + w_, hz), (120 + i * 4, 120, 125), -1)
        # road trapezoid
        cx = W // 2
        road = np.array([[cx - 18, hz], [cx + 18, hz], [W + 220, H], [-220, H]], np.int32)
        cv2.fillPoly(img, [road], (88, 88, 92))
        # lane dashes scroll with odometer
        self._odo += speed * 0.05
        for k in range(14):
            t = ((k * 4.0 - self._odo) % 56.0)           # metres ahead
            d0, d1 = t + 4.0, t + 6.2
            y0, y1 = hz + self.g.ground_k * H / d0, hz + self.g.ground_k * H / d1
            if y0 > H:
                continue
            w0, w1 = max(1, int(5 * 6 / d0 * 1.6)), max(1, int(5 * 6 / d1 * 1.6))
            cv2.fillPoly(img, [np.array([[cx - w0, int(y0)], [cx + w0, int(y0)],
                                         [cx + w1, int(y1)], [cx - w1, int(y1)]], np.int32)], (220, 225, 230))
        # road edge lines
        cv2.line(img, (cx - 18, hz), (-160, H), (200, 205, 210), 2)
        cv2.line(img, (cx + 18, hz), (W + 160, H), (200, 205, 210), 2)
        # potholes (far first)
        truth = []
        vis = []
        for p in self.potholes:
            ok, info = self._project(vlat, vlon, vhdg, p)
            if ok:
                vis.append(info)
        for info in sorted(vis, key=lambda i: -i["d"]):
            x1, y1, x2, y2 = info["bbox"]
            c = ((x1 + x2) / 2, (y1 + y2) / 2)
            ax = (max(1, int((x2 - x1) / 2)), max(1, int((y2 - y1) / 2)))
            cv2.ellipse(img, (int(c[0]), int(c[1])), (ax[0] + 2, ax[1] + 1), 0, 0, 360, (110, 112, 118), -1)
            cv2.ellipse(img, (int(c[0]), int(c[1])), ax, 0, 0, 360, (28, 30, 34), -1)
            truth.append({"bbox": (max(0, x1), max(0, y1), min(W, x2), min(H, y2)),
                          "confidence": round(info["conf"], 3), "distance": info["d"]})
        img = np.clip(img.astype(np.float32) + self._noise, 0, 255).astype(np.uint8)
        # slight vignette-free "dash" hood
        cv2.rectangle(img, (0, H - 6), (W, H), (25, 25, 28), -1)
        return img, truth

    def _read(self):
        gap = (1.0 / self.fps) - (time.time() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.time()
        lat, lon, hdg = self.sim.true_state()
        speed = self.sim.speed_ms * self.sim.time_scale
        img, truth = self._render(lat, lon, hdg, speed)
        return img, {"truth": truth, "demo": True}
