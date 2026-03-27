"""
File Manager — read, write, list, and delete files inside the agent's
workspace (restricted to AGENT_WORKSPACE_DIR, default ~/AgentOS/workspace).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.tools import register

_WORKSPACE = Path(os.getenv("AGENT_WORKSPACE_DIR", Path.home() / "AgentOS" / "workspace"))
_MAX_READ  = 64_000  # 64 KB


def _safe_path(rel: str) -> Path | None:
    """Resolve a relative path within the workspace; reject path traversal."""
    target = (_WORKSPACE / rel).resolve()
    try:
        target.relative_to(_WORKSPACE.resolve())
        return target
    except ValueError:
        return None


@register
class FileManager(Skill):
    name = "file_manager"
    description = (
        "Manage files in the agent workspace. "
        "Params: action ('read'|'write'|'append'|'list'|'delete'|'exists'), "
        "path (str, relative), content (str, for write/append), "
        "encoding (str, optional, default utf-8)."
    )

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        action   = params.get("action", "read").lower()
        rel_path = params.get("path", "").strip()
        content  = params.get("content", "")
        encoding = params.get("encoding", "utf-8")

        if action == "list":
            return self._list(rel_path)

        if not rel_path:
            return {"success": False, "reason": "Missing 'path'."}

        target = _safe_path(rel_path)
        if target is None:
            return {"success": False, "reason": "Path traversal attempt blocked."}

        if action == "read":
            return self._read(target, encoding)
        elif action == "write":
            return self._write(target, content, encoding, append=False)
        elif action == "append":
            return self._write(target, content, encoding, append=True)
        elif action == "delete":
            return self._delete(target)
        elif action == "exists":
            return {"success": True, "exists": target.exists(), "output": str(target.exists())}
        else:
            return {"success": False, "reason": f"Unknown action: {action}"}

    def _read(self, path: Path, encoding: str) -> dict[str, Any]:
        if not path.exists():
            return {"success": False, "reason": f"File not found: {path.name}"}
        if not path.is_file():
            return {"success": False, "reason": f"Not a file: {path.name}"}
        try:
            text = path.read_text(encoding=encoding, errors="replace")[:_MAX_READ]
            return {"success": True, "path": str(path), "content": text, "output": text}
        except Exception as e:
            return {"success": False, "reason": str(e)}

    def _write(self, path: Path, content: str, encoding: str, append: bool) -> dict[str, Any]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            path.open(mode, encoding=encoding).write(content)
            action_word = "Appended to" if append else "Wrote"
            return {
                "success": True,
                "path":    str(path),
                "bytes":   len(content.encode(encoding)),
                "output":  f"{action_word} {path.name} ({len(content)} chars)",
            }
        except Exception as e:
            return {"success": False, "reason": str(e)}

    def _delete(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"success": False, "reason": f"Not found: {path.name}"}
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            return {"success": True, "output": f"Deleted: {path.name}"}
        except Exception as e:
            return {"success": False, "reason": str(e)}

    def _list(self, rel_dir: str) -> dict[str, Any]:
        target = _safe_path(rel_dir) if rel_dir else _WORKSPACE
        if target is None:
            return {"success": False, "reason": "Path traversal attempt blocked."}
        _WORKSPACE.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            return {"success": True, "files": [], "output": "Directory is empty or does not exist."}
        entries = []
        for item in sorted(target.iterdir()):
            kind  = "dir" if item.is_dir() else "file"
            size  = item.stat().st_size if item.is_file() else 0
            entries.append({"name": item.name, "type": kind, "size": size})
        lines  = [f"{'Name':<40} {'Type':<6} {'Size':>8}"]
        lines += [f"{e['name']:<40} {e['type']:<6} {e['size']:>8}" for e in entries]
        return {"success": True, "files": entries, "output": "\n".join(lines)}
