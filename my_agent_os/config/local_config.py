"""
Local-first single-user config manager.

所有凭证和偏好保存在 ~/.coreclaw/ 下，绝不上云。
与 OpenClaw 一致：网关主机（本地 Mac mini 或 VPS）是绝对信任边界。

目录结构:
  ~/.coreclaw/
    config.json          # 模型提供商、功能开关、API Keys
    MEMORY.md            # L3 长效记忆（人类可读 Markdown）
    skills/              # 外部 SKILL.md 插件包
      <skill_name>/
        SKILL.md
    sessions/            # JSONL 会话缓存（按 user_id 分文件）
      <user_id>.jsonl
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CORECLAW_DIR = Path.home() / ".coreclaw"

_DEFAULT_CONFIG: dict[str, Any] = {
    "llm_provider": "deepseek",       # deepseek | openai | anthropic | gemini | ollama
    "llm_model": "",                   # 空 → 使用各 provider 默认模型
    "ollama_base_url": "http://localhost:11434",
    "openai_api_key": "",
    "anthropic_api_key": "",
    "gemini_api_key": "",
    "context_window_tokens": 8192,
    "compaction_threshold": 0.80,
    "lane_queue_workers": 4,
    "skill_lazy_load": True,
    "memory_md_enabled": True,
    "embeddings_enabled": True,
    "version": "2.0.0",
}


class LocalConfig:
    """
    单用户本地优先配置，由 ~/.coreclaw/config.json 支撑。
    启动时加载，写操作立即落盘（write-through）。
    """

    def __init__(self, base_dir: Path | None = None):
        self._dir: Path | None = base_dir or _CORECLAW_DIR
        self._data: dict[str, Any] = {}
        self._ensure_dirs()
        # After _ensure_dirs, self._dir might be None if not writable
        self._cfg_path = (self._dir / "config.json") if self._dir else Path("/dev/null")
        self._load()

    def _ensure_dirs(self) -> None:
        try:
            for sub in ("", "skills", "sessions"):
                (self._dir / sub).mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as exc:
            logger.warning(
                "LocalConfig: 无法创建 %s （将以内存模式运行）: %s", self._dir, exc
            )
            self._dir = None  # type: ignore[assignment]

    def _load(self) -> None:
        if self._dir is None:
            self._data = dict(_DEFAULT_CONFIG)
            return
        if self._cfg_path.exists():
            try:
                loaded = json.loads(self._cfg_path.read_text(encoding="utf-8"))
                self._data = {**_DEFAULT_CONFIG, **loaded}
                return
            except Exception as e:
                logger.warning("LocalConfig: config.json 损坏，使用默认值: %s", e)
        self._data = dict(_DEFAULT_CONFIG)
        self._save()

    def _save(self) -> None:
        if self._dir is None:
            return  # 内存模式，不持久化
        try:
            self._cfg_path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except (PermissionError, OSError) as exc:
            logger.debug("LocalConfig: 无法写入 config.json: %s", exc)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    def update(self, patch: dict[str, Any]) -> None:
        self._data.update(patch)
        self._save()

    @property
    def coreclaw_dir(self) -> Path | None:
        return self._dir

    @property
    def memory_md_path(self) -> Path | None:
        return (self._dir / "MEMORY.md") if self._dir else None

    @property
    def skills_dir(self) -> Path | None:
        return (self._dir / "skills") if self._dir else None

    @property
    def sessions_dir(self) -> Path | None:
        return (self._dir / "sessions") if self._dir else None

    @property
    def llm_provider(self) -> str:
        return str(self._data.get("llm_provider", "deepseek"))

    @property
    def llm_model(self) -> str:
        return str(self._data.get("llm_model", ""))

    @property
    def context_window_tokens(self) -> int:
        return int(self._data.get("context_window_tokens", 8192))

    @property
    def compaction_threshold(self) -> float:
        return float(self._data.get("compaction_threshold", 0.80))

    @property
    def embeddings_enabled(self) -> bool:
        return bool(self._data.get("embeddings_enabled", True))

    @property
    def memory_md_enabled(self) -> bool:
        return bool(self._data.get("memory_md_enabled", True))


_local_config: LocalConfig | None = None


def get_local_config() -> LocalConfig:
    global _local_config
    if _local_config is None:
        _local_config = LocalConfig()
    return _local_config
