"""
Channel Policies — OpenClaw-style DM/group access control.

Loads from channels.yaml, supports pairing store for pairing policy.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "channels.yaml"
_PAIRING_PATH = Path(__file__).parent.parent / "memory_layer" / "data" / "channel_pairing.json"


def _load_channels_config() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_whatsapp_config() -> dict:
    """Get WhatsApp channel config with defaults."""
    cfg = _load_channels_config()
    return cfg.get("whatsapp", {})


def _load_pairing_store() -> dict:
    if not _PAIRING_PATH.exists():
        return {}
    try:
        with open(_PAIRING_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to load pairing store: %s", e)
        return {}


def _save_pairing_store(data: dict) -> None:
    _PAIRING_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PAIRING_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def is_paired(channel: str, identifier: str) -> bool:
    """Check if identifier is paired for channel."""
    store = _load_pairing_store()
    channel_data = store.get(channel, {})
    return identifier in channel_data.get("paired", [])


def add_paired(channel: str, identifier: str) -> None:
    """Add identifier to pairing store."""
    store = _load_pairing_store()
    if channel not in store:
        store[channel] = {"paired": []}
    if identifier not in store[channel]["paired"]:
        store[channel]["paired"].append(identifier)
        _save_pairing_store(store)


def get_pending_pairing(channel: str) -> list[dict]:
    """Get pending pairing requests."""
    store = _load_pairing_store()
    return store.get(channel, {}).get("pending", [])
