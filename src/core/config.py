from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "events.sqlite3"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
