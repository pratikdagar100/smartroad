import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402
from database.db import Database  # noqa: E402


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


@pytest.fixture
def cfg(tmp_path):
    return load_config(overrides={
        "database": {"path": str(tmp_path / "t.db")},
        "demo": {"db_path": str(tmp_path / "demo.db"), "enabled": False},
        "hazards": {"image_dir": str(tmp_path / "img")},
        "app": {"log_dir": str(tmp_path / "logs")},
        "gps": {"mode": "simulated"},
        "camera": {"backend": "demo"},
    })
