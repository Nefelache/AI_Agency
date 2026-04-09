"""
Skill Base Class — the DNA of every 'Limb'.

Rules:
  - All skills MUST be stateless.
  - All skills MUST return a dict.
  - Registration happens via the @register decorator in tools/__init__.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal, TypedDict


class SkillResult(TypedDict, total=False):
    ok: bool
    code: str
    message: str
    output: str
    data: dict[str, Any]
    provider: str
    retryable: bool
    legacy: dict[str, Any]


class Skill(ABC):
    name: ClassVar[str]
    description: ClassVar[str]

    @abstractmethod
    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run the skill. Must not hold state between invocations."""
        ...


def skill_ok(
    message: str,
    *,
    code: str = "OK",
    output: str | None = None,
    data: dict[str, Any] | None = None,
    provider: str | None = None,
) -> SkillResult:
    data_obj = data or {}
    out: SkillResult = {
        "ok": True,
        "code": code,
        "message": message,
        "output": output if output is not None else message,
        "data": data_obj,
        "success": True,  # backward compatibility for existing skills/tests
        "reason": "",
        "legacy": {
            "success": True,
            "reason": "",
            "output": output if output is not None else message,
            **data_obj,
        },
    }
    if provider:
        out["provider"] = provider
        out["legacy"]["provider"] = provider
    return out


def skill_err(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    output: str | None = None,
    data: dict[str, Any] | None = None,
    provider: str | None = None,
) -> SkillResult:
    data_obj = data or {}
    out: SkillResult = {
        "ok": False,
        "code": code,
        "message": message,
        "output": output if output is not None else message,
        "retryable": retryable,
        "data": data_obj,
        "success": False,  # backward compatibility for existing skills/tests
        "reason": message,
        "legacy": {
            "success": False,
            "reason": message,
            "output": output if output is not None else message,
            **data_obj,
        },
    }
    if provider:
        out["provider"] = provider
        out["legacy"]["provider"] = provider
    return out


def normalize_skill_result(raw: dict[str, Any]) -> SkillResult:
    """
    Normalize legacy skill return payloads to SkillResult.
    Keeps backward compatibility while router gradually adopts v2.
    """
    if "ok" in raw and "code" in raw and "message" in raw:
        out: SkillResult = {
            "ok": bool(raw.get("ok")),
            "code": str(raw.get("code")),
            "message": str(raw.get("message")),
            "output": str(raw.get("output", raw.get("message", ""))),
            "retryable": bool(raw.get("retryable", False)),
            "data": raw.get("data", {}) if isinstance(raw.get("data"), dict) else {},
        }
        if isinstance(raw.get("provider"), str):
            out["provider"] = raw["provider"]
        out["legacy"] = raw
        return out

    ok = bool(raw.get("success", True))
    if ok:
        msg = str(raw.get("output") or raw.get("message") or "Skill executed successfully.")
        return {
            "ok": True,
            "code": "OK",
            "message": msg,
            "output": msg,
            "data": {},
            "legacy": raw,
        }

    reason = str(raw.get("reason") or raw.get("message") or "Skill execution failed.")
    status = raw.get("status")
    code = "SKILL_FAILED"
    retryable = False
    if isinstance(status, int):
        if status == 429:
            code = "RATE_LIMIT"
            retryable = True
        elif status >= 500:
            code = "UPSTREAM_ERROR"
            retryable = True
        elif status in (401, 403):
            code = "AUTH_ERROR"
    return {
        "ok": False,
        "code": code,
        "message": reason,
        "output": reason,
        "retryable": retryable,
        "data": {},
        "legacy": raw,
    }
