"""
Global Settings — single source of truth for all runtime configuration.

Reads from environment variables (via .env) with sensible local-first defaults.
No secret should ever be hard-coded here.
"""

from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


_ENV_FILE = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Server ---
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # --- LLM: DeepSeek ---
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # --- Auth ---
    API_KEY_OWNER: str = ""
    API_KEY_CHANNEL: str = ""
    API_KEY_GUEST: str = ""
    RATE_LIMIT_PER_MINUTE: int = 30

    # --- Memory System ---
    MEMORY_DB_PATH: str = "my_agent_os/memory_layer/data/memory.db"
    MEMORY_RETRIEVAL_TOP_K: int = 5
    MEMORY_PRIORITY_DECAY_DAYS: float = 7.0
    MEMORY_MAX_INJECTION_CHARS: int = 2000

    # --- Enterprise: Audit ---
    AUDIT_ENABLED: bool = True
    AUDIT_RETENTION_DAYS: int = 90

    # --- Network Proxy (for regions with restricted access) ---
    HTTPS_PROXY: str = ""

    # --- MQTT (reserved for edge devices) ---
    MQTT_BROKER: str = "localhost"
    MQTT_PORT: int = 1883

    # --- WhatsApp ---
    WHATSAPP_BRIDGE_SECRET: str = ""  # Shared secret for Baileys bridge
    WHATSAPP_ALLOW_FROM: str = ""  # Comma-separated E.164 numbers (e.g. +15551234567,+8613800138000)
    # Cloud API (PyWa) — optional, for Meta Business
    WHATSAPP_PHONE_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_VERIFY_TOKEN: str = "agent-os-verify"
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = ""


settings = Settings()
