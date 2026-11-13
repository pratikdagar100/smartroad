"""Plain data types shared by the database layer and the API."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

STATUS_ACTIVE = "ACTIVE"
STATUS_FIXED = "FIXED"
STATUS_UNKNOWN = "UNKNOWN"
STATUSES = (STATUS_ACTIVE, STATUS_FIXED, STATUS_UNKNOWN)

SEV_LOW, SEV_MEDIUM, SEV_HIGH = "LOW", "MEDIUM", "HIGH"
SEVERITIES = (SEV_LOW, SEV_MEDIUM, SEV_HIGH)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso_to_epoch(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None


@dataclass
class Hazard:
    id: int
    latitude: float
    longitude: float
    hazard_type: str
    severity: str
    severity_score: float
    confidence: float
    image_path: Optional[str]
    first_detected: str
    last_detected: str
    detection_count: int
    status: str
    source: str
    heading: Optional[float]
    speed: Optional[float]
    verified: int = 0
    route_id: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> "Hazard":
        return cls(**{k: row[k] for k in cls.__dataclass_fields__})  # type: ignore[attr-defined]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
