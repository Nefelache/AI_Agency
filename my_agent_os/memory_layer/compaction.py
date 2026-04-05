"""
上下文窗口守卫 + 压缩器 — OpenClaw Context Window Guard 的 Python 实现。

功能:
  1. 估算对话历史占用的 Token 数（无需 tiktoken）。
  2. 当历史超过阈值（默认 80% 上限）时触发压缩。
  3. 压缩策略（按顺序）:
       a. 丢弃纯代码块或工具输出的 assistant 消息。
       b. 截断过长的 assistant 消息（保留前 800 字符）。
       c. 若仍超标：将最老的 40% 轮次折叠为摘要存根。

JSONL 会话缓存 (~/.coreclaw/sessions/<user_id>.jsonl):
  每行一个 JSON turn: {"role": "user"|"assistant", "content": "..."}
  独立于 SQLite 记忆库；SQLite 负责提炼长期事实，JSONL 负责滚动上下文窗口。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 每字符约对应 token 数（EN ≈ 1/4, ZH ≈ 2/3；取保守均值）
_CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    """轻量 Token 估算（无 tiktoken 依赖）。"""
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def count_history_tokens(history: list[dict]) -> int:
    return sum(estimate_tokens(t.get("content", "")) for t in history)


class ContextWindowGuard:
    """
    监控 Token 预算，超阈值时触发对话历史压缩。

    max_tokens:  总上下文窗口（默认 8192）
    threshold:   触发压缩的比例（默认 0.80）
    reserve:     为 system prompt + 新回答预留的 token（默认 2048）
    """

    def __init__(
        self,
        max_tokens: int = 8192,
        threshold: float = 0.80,
        reserve: int = 2048,
    ):
        self.max_tokens = max_tokens
        self.threshold = threshold
        self.reserve = reserve
        self._trigger = int(max_tokens * threshold) - reserve

    def should_compact(self, history: list[dict]) -> bool:
        return count_history_tokens(history) >= self._trigger

    def compact(self, history: list[dict]) -> list[dict]:
        """
        三阶段压缩策略:
          1. 丢弃纯工具回显轮次
          2. 截断过长 assistant 消息
          3. 折叠最老 40% 轮次为摘要
        """
        h = list(history)
        before = len(h)
        h = self._drop_tool_echo_turns(h)
        h = self._truncate_long_assistant_turns(h)
        if count_history_tokens(h) >= self._trigger:
            h = self._collapse_oldest(h)
        logger.info(
            "Compaction: %d → %d 轮（%d tokens）",
            before,
            len(h),
            count_history_tokens(h),
        )
        return h

    # ── 压缩子步骤 ─────────────────────────────────────

    @staticmethod
    def _drop_tool_echo_turns(history: list[dict]) -> list[dict]:
        """丢弃纯代码块或工具 JSON dump 的 assistant 轮次。"""
        _code_fence_re = re.compile(r"^```[\s\S]*```\s*$")
        result = []
        for turn in history:
            if turn.get("role") == "assistant":
                c = turn.get("content", "").strip()
                if _code_fence_re.match(c) and len(c) > 200:
                    continue
                try:
                    json.loads(c)
                    if len(c) > 300:
                        continue
                except (json.JSONDecodeError, ValueError):
                    pass
            result.append(turn)
        return result

    @staticmethod
    def _truncate_long_assistant_turns(
        history: list[dict], max_chars: int = 800
    ) -> list[dict]:
        """截断超长 assistant 消息。"""
        result = []
        for turn in history:
            if turn.get("role") == "assistant":
                c = turn.get("content", "")
                if len(c) > max_chars:
                    turn = {**turn, "content": c[:max_chars] + " …[已截断]"}
            result.append(turn)
        return result

    @staticmethod
    def _collapse_oldest(history: list[dict]) -> list[dict]:
        """将最老的 40% 轮次折叠为单条摘要存根。"""
        if len(history) < 4:
            return history
        cut = max(2, len(history) * 4 // 10)
        oldest = history[:cut]
        tail = history[cut:]
        stub = {
            "role": "assistant",
            "content": f"[已压缩早期上下文 — 省略 {len(oldest)} 轮对话]",
        }
        return [stub] + tail


# ── JSONL 会话缓存 ────────────────────────────────────────────────────────────


class JsonlSessionCache:
    """
    按用户独立的 JSONL 滚动会话缓存。
    路径: ~/.coreclaw/sessions/<safe_user_id>.jsonl

    用途: 维护短期对话上下文窗口，配合 ContextWindowGuard 自动压缩。
    与 SQLite 记忆库互补：JSONL 存活跃对话，SQLite 提炼长期事实。
    """

    def __init__(self, sessions_dir: Path, user_id: str, max_turns: int = 200):
        safe_id = re.sub(r"[^\w\-]", "_", user_id)[:64]
        self._path = sessions_dir / f"{safe_id}.jsonl"
        self._max_turns = max_turns

    def append_turn(self, role: str, content: str) -> None:
        turn = json.dumps({"role": role, "content": content}, ensure_ascii=False)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(turn + "\n")

    def read_turns(self, tail: int | None = None) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        turns: list[dict[str, Any]] = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        turns.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return turns[-tail:] if tail and len(turns) > tail else turns

    def replace_all(self, turns: list[dict[str, Any]]) -> None:
        """用压缩后的轮次列表覆盖整个文件。"""
        lines = [json.dumps(t, ensure_ascii=False) for t in turns]
        self._path.write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )

    def compact_if_needed(self, guard: ContextWindowGuard) -> int:
        """
        检查 Token 预算，超出时执行压缩并回写。
        返回压缩后的轮次数（未压缩返回当前轮次数）。
        """
        turns = self.read_turns()
        if guard.should_compact(turns):
            compacted = guard.compact(turns)
            self.replace_all(compacted)
            return len(compacted)
        return len(turns)
