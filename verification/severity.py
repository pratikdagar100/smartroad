"""Explainable *estimated* severity.

IMPORTANT: bounding-box size is NOT pothole depth.  The score below is a
weighted heuristic and is always presented as "Estimated severity" until a
real depth sensor exists.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

LOW, MEDIUM, HIGH = "LOW", "MEDIUM", "HIGH"


def classify(score: float, medium: float = 0.35, high: float = 0.65) -> str:
    if score >= high:
        return HIGH
    if score >= medium:
        return MEDIUM
    return LOW


def estimate_severity(sev_cfg, bbox_norm: Tuple[float, float, float, float], confidence: float,
                      distance_m: Optional[float] = None, detection_count: int = 1,
                      impact_g: Optional[float] = None, impact_threshold_g: float = 1.6
                      ) -> Tuple[float, str, Dict[str, float]]:
    """Return ``(score 0-1, LOW/MEDIUM/HIGH, per-term breakdown)``.

    Terms whose input is unavailable (distance, IMU) are dropped and the
    remaining weights re-normalised, so a missing sensor never lowers the score.
    """
    area = max(0.0, (bbox_norm[2] - bbox_norm[0])) * max(0.0, (bbox_norm[3] - bbox_norm[1]))
    terms = {
        "area": (sev_cfg.w_area, min(1.0, area / max(1e-6, sev_cfg.area_full_scale))),
        "confidence": (sev_cfg.w_confidence, max(0.0, min(1.0, confidence))),
        "repeat": (sev_cfg.w_repeat, min(1.0, (max(1, detection_count) - 1) / 4.0)),
    }
    if distance_m is not None and distance_m > 0:
        terms["proximity"] = (sev_cfg.w_proximity, max(0.0, min(1.0, 1.0 - distance_m / 40.0)))
    if impact_g is not None:
        terms["impact"] = (sev_cfg.w_impact, max(0.0, min(1.0, impact_g / (2.0 * impact_threshold_g))))
    wsum = sum(w for w, _ in terms.values()) or 1.0
    breakdown = {k: round(w * v / wsum, 3) for k, (w, v) in terms.items()}
    score = round(sum(breakdown.values()), 3)
    return score, classify(score, sev_cfg.medium_threshold, sev_cfg.high_threshold), breakdown
