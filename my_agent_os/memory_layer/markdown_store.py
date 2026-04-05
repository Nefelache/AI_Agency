"""
L3 Markdown Memory Store — MEMORY.md 长效记忆持久化。

OpenClaw 设计：用户的核心偏好、历史决策和高价值事实以人类可读的
Markdown 文件保存，跨进程重启保持持久化，支持直接编辑。

文件位置: ~/.coreclaw/MEMORY.md
章节示例:
  ## Core Preferences     # 核心偏好
  ## Key Decisions        # 关键决策
  ## Important Facts      # 重要事实
  ## Active Goals         # 当前目标
  ## Recent Highlights    # 近期亮点
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# 章节标题正则
_SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)

# 默认章节（优先级顺序决定 prompt 注入顺序）
DEFAULT_SECTIONS = [
    "Core Preferences",
    "Key Decisions",
    "Important Facts",
    "Active Goals",
    "Recent Highlights",
]

_EMPTY_PLACEHOLDER = "_(empty)_"


class MarkdownMemoryStore:
    """
    读写 ~/.coreclaw/MEMORY.md 的 L3 长效记忆层。
    单用户单进程设计，直接文件 I/O，无需加锁。
    """

    def __init__(self, path: Path):
        self._path = path
        if not path.exists():
            self._init_file()

    def _init_file(self) -> None:
        lines = ["# CoreClaw Long-term Memory\n\n"]
        for section in DEFAULT_SECTIONS:
            lines.append(f"## {section}\n\n{_EMPTY_PLACEHOLDER}\n\n")
        self._path.write_text("".join(lines), encoding="utf-8")
        logger.info("已初始化 MEMORY.md: %s", self._path)

    # ── 读取 ──────────────────────────────────────────────

    def read_all(self) -> str:
        """返回完整 MEMORY.md 内容。"""
        try:
            return self._path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def read_sections(self) -> dict[str, str]:
        """将 MEMORY.md 解析为 {section_name: body_text} 字典。"""
        text = self.read_all()
        sections: dict[str, str] = {}
        matches = list(_SECTION_RE.finditer(text))
        for i, m in enumerate(matches):
            name = m.group(1).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            sections[name] = text[start:end].strip()
        return sections

    def snapshot_for_prompt(self, max_chars: int = 1200) -> str:
        """
        生成适合注入 system prompt 的紧凑快照。
        优先级: Core Preferences > Key Decisions > Important Facts > …
        """
        sections = self.read_sections()
        parts: list[str] = []
        total = 0
        for section in DEFAULT_SECTIONS:
            body = sections.get(section, "").strip()
            if not body or body == _EMPTY_PLACEHOLDER:
                continue
            block = f"**{section}**\n{body}"
            if total + len(block) > max_chars:
                remaining = max_chars - total - len(f"**{section}**\n") - 1
                if remaining > 60:
                    parts.append(f"**{section}**\n{body[:remaining]}…")
                break
            parts.append(block)
            total += len(block) + 2
        return "\n\n".join(parts)

    # ── 写入 ──────────────────────────────────────────────

    def upsert_fact(
        self,
        fact: str,
        section: str = "Important Facts",
        deduplicate: bool = True,
    ) -> None:
        """在指定章节追加一条 bullet 事实（支持去重）。"""
        sections = self.read_sections()
        body = sections.get(section, "")
        if deduplicate and fact.strip() in body:
            return
        body = body.replace(_EMPTY_PLACEHOLDER, "").strip()
        bullet = f"- {fact.strip()}"
        sections[section] = (body + "\n" + bullet).strip() if body else bullet
        self._write_sections(sections)

    def set_section(self, section: str, content: str) -> None:
        """整体替换某章节内容。"""
        sections = self.read_sections()
        sections[section] = content.strip()
        self._write_sections(sections)

    def _write_sections(self, sections: dict[str, str]) -> None:
        lines = ["# CoreClaw Long-term Memory\n\n"]
        written: set[str] = set()
        for section in DEFAULT_SECTIONS:
            if section in sections:
                body = sections[section] or _EMPTY_PLACEHOLDER
                lines.append(f"## {section}\n\n{body}\n\n")
                written.add(section)
        # 追加非标准章节
        for section, body in sections.items():
            if section not in written:
                lines.append(f"## {section}\n\n{body or _EMPTY_PLACEHOLDER}\n\n")
        self._path.write_text("".join(lines), encoding="utf-8")
