"""
SKILL.md 插件加载器 — OpenClaw Skills 按需懒加载的 Python 实现。

每个技能包是一个目录，包含 SKILL.md：
  ~/.coreclaw/skills/<skill_name>/SKILL.md

SKILL.md 格式:
  # 技能名称（可含中文）
  description: 一行描述（注入 list_tools()）

  ## Instructions
  （注入 LLM 系统提示词的技能使用说明）

  ## Execute
  ```python
  # 可选：定义 async def execute(params: dict) -> dict:
  # 若存在则注册为可调用工具；否则为纯指令型技能
  async def execute(params: dict) -> dict:
      return {"success": True, "output": params.get("input", "")}
  ```

懒加载：发现阶段仅解析 SKILL.md，代码在首次 execute() 时才被 exec。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import textwrap
from pathlib import Path
from typing import Any

from my_agent_os.skills_layer.base import Skill

logger = logging.getLogger(__name__)

_loaded_packs: dict[str, "ExternalSkill"] = {}


class ExternalSkill(Skill):
    """
    从 SKILL.md 动态加载的插件技能。

    execute 实现来源：
      - ## Execute 段的 Python 代码块（首次调用时 exec，之后缓存）
      - 若无代码块：返回 instructions 内容（纯指令型技能）
    """

    name: str = "_external"
    description: str = ""
    skill_instructions: str = ""

    def __init__(
        self,
        skill_name: str,
        description: str,
        instructions: str,
        execute_code: str | None,
    ):
        # 动态覆盖类级属性（每个实例对应一个独立子类，因此安全）
        self.__class__ = type(
            f"ExternalSkill_{skill_name}",
            (ExternalSkill,),
            {
                "name": skill_name,
                "description": description,
                "skill_instructions": instructions,
            },
        )
        self._execute_code = execute_code
        self._compiled_fn: Any = None

    async def execute(self, params: dict) -> dict:
        if self._execute_code:
            fn = self._get_compiled_fn()
            if fn:
                try:
                    if inspect.iscoroutinefunction(fn):
                        result = await fn(params)
                    else:
                        result = fn(params)
                    return result if isinstance(result, dict) else {"success": True, "output": str(result)}
                except Exception as exc:
                    logger.warning("ExternalSkill '%s' execute 失败: %s", self.name, exc)
                    return {"success": False, "reason": str(exc)}
        return {
            "success": True,
            "output": self.skill_instructions or f"技能 '{self.name}' 为纯指令型，无可执行代码。",
        }

    def _get_compiled_fn(self) -> Any:
        if self._compiled_fn is not None:
            return self._compiled_fn
        try:
            ns: dict[str, Any] = {"asyncio": asyncio}
            exec(textwrap.dedent(self._execute_code), ns)  # noqa: S102
            self._compiled_fn = ns.get("execute")
            return self._compiled_fn
        except Exception as exc:
            logger.error("ExternalSkill '%s' 编译失败: %s", self.name, exc)
            return None


# ── SKILL.md 解析 ─────────────────────────────────────────────────────────────


def _parse_skill_md(path: Path) -> dict[str, str]:
    """解析 SKILL.md，返回 {name, description, instructions, execute_code}。"""
    text = path.read_text(encoding="utf-8")
    result: dict[str, str] = {
        "name": path.parent.name,
        "description": "",
        "instructions": "",
        "execute_code": "",
    }
    # H1 → 技能名
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        result["name"] = m.group(1).strip()
    # description: 字段
    m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if m:
        result["description"] = m.group(1).strip()
    # ## Instructions 段
    m = re.search(r"^##\s+Instructions\s*\n([\s\S]*?)(?=^##|\Z)", text, re.MULTILINE)
    if m:
        result["instructions"] = m.group(1).strip()
    # ## Execute 段中的 Python 代码块
    m = re.search(r"^##\s+Execute\s*\n```python\n([\s\S]*?)```", text, re.MULTILINE)
    if m:
        result["execute_code"] = m.group(1)
    return result


# ── 发现与注册 ────────────────────────────────────────────────────────────────


def discover_external_skills(skills_dir: Path) -> list[ExternalSkill]:
    """
    扫描 ~/.coreclaw/skills/ 下所有含 SKILL.md 的目录，加载并返回技能实例。
    已加载的技能名跳过（防重复）。
    """
    skills: list[ExternalSkill] = []
    if not skills_dir.exists():
        return skills

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        md_path = skill_dir / "SKILL.md"
        if not md_path.exists():
            continue
        try:
            parsed = _parse_skill_md(md_path)
            skill_name = re.sub(r"[^\w\-]", "_", parsed["name"]).lower()
            if skill_name in _loaded_packs:
                continue
            skill = ExternalSkill(
                skill_name=skill_name,
                description=parsed["description"],
                instructions=parsed["instructions"],
                execute_code=parsed["execute_code"] or None,
            )
            _loaded_packs[skill_name] = skill
            skills.append(skill)
            logger.info("已加载外部技能: '%s'  ← %s", skill_name, md_path)
        except Exception as exc:
            logger.warning("加载技能失败 %s: %s", md_path, exc)

    return skills


def get_loaded_external_skills() -> dict[str, ExternalSkill]:
    """返回已发现的外部技能字典（需先调用 discover_external_skills）。"""
    return _loaded_packs
