"""Light IoU tracker with velocity prediction.

A hazard approaching the camera grows and moves down the frame, so each
track predicts its next box from recent motion.  A track is *confirmed*
exactly once, after ``min_stable_frames`` hits - this is the debouncing that
turns 20 consecutive frames into ONE candidate event.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import List, Tuple

from .postprocess import Detection

Box = Tuple[float, float, float, float]


def iou(a: Box, b: Box) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


@dataclass
class Track:
    id: int
    cls_id: int
    cls_name: str
    bbox: Box
    confidence: float
    hits: int = 1
    misses: int = 0
    confirmed: bool = False
    velocity: Box = (0.0, 0.0, 0.0, 0.0)

    def predicted(self) -> Box:
        v = self.velocity
        return (self.bbox[0] + v[0], self.bbox[1] + v[1], self.bbox[2] + v[2], self.bbox[3] + v[3])


class Tracker:
    def __init__(self, min_stable_frames: int = 5, max_misses: int = 5, match_iou: float = 0.15):
        self.min_stable = max(1, min_stable_frames)
        self.max_misses = max_misses
        self.match_iou = match_iou
        self.tracks: List[Track] = []
        self._ids = itertools.count(1)

    def update(self, detections: List[Detection]) -> List[Track]:
        """Advance one inference step. Returns tracks that became confirmed *now*."""
        newly: List[Track] = []
        pairs = []
        for ti, t in enumerate(self.tracks):
            pred = t.predicted()
            for di, d in enumerate(detections):
                if d.cls_id != t.cls_id:
                    continue
                s = max(iou(pred, d.bbox), iou(t.bbox, d.bbox))
                if s >= self.match_iou:
                    pairs.append((s, ti, di))
        used_t, used_d = set(), set()
        for _s, ti, di in sorted(pairs, reverse=True):
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            t, d = self.tracks[ti], detections[di]
            t.velocity = tuple(0.5 * (n - o) + 0.5 * v   # type: ignore[assignment]
                               for n, o, v in zip(d.bbox, t.bbox, t.velocity))
            t.bbox = d.bbox
            t.hits += 1
            t.misses = 0
            t.confidence = 0.6 * t.confidence + 0.4 * d.confidence  # temporal smoothing
            if not t.confirmed and t.hits >= self.min_stable:
                t.confirmed = True
                newly.append(t)
        for ti, t in enumerate(self.tracks):
            if ti not in used_t:
                t.misses += 1
        self.tracks = [t for t in self.tracks if t.misses <= self.max_misses]
        for di, d in enumerate(detections):
            if di in used_d:
                continue
            t = Track(next(self._ids), d.cls_id, d.cls_name, d.bbox, d.confidence)
            if self.min_stable <= 1:
                t.confirmed = True
                newly.append(t)
            self.tracks.append(t)
        return newly

    def active(self) -> List[Track]:
        return [t for t in self.tracks if t.misses == 0]
