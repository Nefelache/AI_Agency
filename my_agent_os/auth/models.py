"""
Auth Models — Roles, identity context, and permission matrix.

Inspired by OpenClaw's security failures:
  - Least privilege by default
  - Every request carries an AuthContext
  - Destructive operations require OWNER role
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from pydantic import BaseModel


class Role(str, Enum):
    OWNER = "owner"
    CHANNEL = "channel"
    GUEST = "guest"


class AuthContext(BaseModel):
    """Injected into every request via FastAPI Depends."""
    user_id: str
    role: Role
    api_key_id: str = ""


ENDPOINT_GROUPS: dict[str, list[Role]] = {
    "console_query": [Role.OWNER, Role.CHANNEL, Role.GUEST],
    "mobile_webhook": [Role.OWNER, Role.CHANNEL],
    "memory_read": [Role.OWNER, Role.CHANNEL],
    "memory_write": [Role.OWNER],
    "memory_delete": [Role.OWNER],
    "memory_seal": [Role.OWNER],
    "health": [Role.OWNER, Role.CHANNEL, Role.GUEST],
}


def has_permission(role: Role, endpoint_group: str) -> bool:
    allowed = ENDPOINT_GROUPS.get(endpoint_group, [])
    return role in allowed
