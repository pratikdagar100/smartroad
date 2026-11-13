"""Optional privacy filter applied to images BEFORE they are written to disk.

Uses the Haar cascades bundled with OpenCV (faces, and a generic plate
cascade).  They are coarse - swap in a better detector later through the same
``PrivacyFilter.apply`` interface.  Nothing is ever uploaded automatically.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

log = logging.getLogger("smartroad.privacy")


class PrivacyFilter:
    def __init__(self, enabled: bool = False, blur_faces: bool = True, blur_plates: bool = True):
        self.enabled = enabled
        self._cascades = []
        if not enabled:
            return
        base = cv2.data.haarcascades
        names = []
        if blur_faces:
            names.append("haarcascade_frontalface_default.xml")
        if blur_plates:
            names.append("haarcascade_russian_plate_number.xml")
        for n in names:
            c = cv2.CascadeClassifier(base + n)
            if c.empty():
                log.warning("Privacy cascade %s unavailable", n)
            else:
                self._cascades.append(c)

    def apply(self, img: np.ndarray) -> np.ndarray:
        if not self.enabled or not self._cascades:
            return img
        out = img.copy()
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for c in self._cascades:
            for (x, y, w, h) in c.detectMultiScale(gray, 1.1, 5, minSize=(24, 12)):
                roi = out[y:y + h, x:x + w]
                k = max(3, (min(w, h) // 3) | 1)
                out[y:y + h, x:x + w] = cv2.GaussianBlur(roi, (k, k), 0)
        return out
