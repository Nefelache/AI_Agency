from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Load both repo-level and backend-specific .env files (if they exist)
for candidate in (PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"):
    if candidate.exists():
        load_dotenv(dotenv_path=candidate, override=False)


class Settings:
    def __init__(self) -> None:
        self.project_root: Path = PROJECT_ROOT
        self.data_dir: Path = self.project_root / "data"
        self.db_path: Path = self.data_dir / "watch_history.sqlite3"
        self.database_url: str = f"sqlite:///{self.db_path}"
        self.api_prefix: str = "/api"
        self.bilibili_cookie: str | None = os.getenv("BILIBILI_COOKIE")
        self.request_timeout: float = float(os.getenv("BILIBILI_TIMEOUT", "10"))
        self.deepseek_api_key: str | None = os.getenv("DEEPSEEK_API_KEY")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
