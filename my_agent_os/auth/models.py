"""
Auth Models — Roles, identity context, and permission matrix.

Inspired by OpenClaw's security failures:
  - Least privilege by default
  - Every request carries an AuthContext
  - Destructive operations require ROOT (admin)
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Role(str, Enum):
    """Human dashboard: root vs employee. Integrations: channel / guest API keys."""

    ROOT = "root"
    EMPLOYEE = "employee"
    CHANNEL = "channel"
    GUEST = "guest"


class AuthContext(BaseModel):
    """Injected into every request via FastAPI Depends."""
    user_id: str
    role: Role
    api_key_id: str = ""


ENDPOINT_GROUPS: dict[str, list[Role]] = {
    "console_query": [Role.ROOT, Role.EMPLOYEE, Role.CHANNEL, Role.GUEST],
    "mobile_webhook": [Role.ROOT, Role.CHANNEL],
    "memory_read": [Role.ROOT, Role.EMPLOYEE, Role.CHANNEL],
    "memory_write": [Role.ROOT],
    "memory_delete": [Role.ROOT],
    "memory_seal": [Role.ROOT],
    "health": [Role.ROOT, Role.EMPLOYEE, Role.CHANNEL, Role.GUEST],
}


def has_permission(role: Role, endpoint_group: str) -> bool:
    allowed = ENDPOINT_GROUPS.get(endpoint_group, [])
    return role in allowed
