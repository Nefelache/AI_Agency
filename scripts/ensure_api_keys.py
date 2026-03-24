#!/usr/bin/env python3
"""
Ensure Agent OS API keys exist in my_agent_os/config/.env (non-interactive).

Use when:
  - You deployed from .env.example and still have placeholders
  - Redeploy did not run interactive setup.py
  - You need new random OWNER / CHANNEL / GUEST keys without prompts

Does NOT overwrite non-placeholder values. Run on the host (same machine that holds .env).

Usage:
  python scripts/ensure_api_keys.py
  python scripts/ensure_api_keys.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from secrets import token_hex

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / "my_agent_os" / "config" / ".env"
EXAMPLE_PATH = ROOT / "my_agent_os" / "config" / ".env.example"

# Treat as "needs generation"
_PLACEHOLDERS = frozenset(
    {
        "",
        "your-owner-key",
        "your-channel-key",
        "your-guest-key",
        "your-deepseek-api-key",
        "changeme",
    }
)

_KEY_NAMES = ("API_KEY_OWNER", "API_KEY_CHANNEL", "API_KEY_GUEST")


def _gen_owner_like(prefix: str) -> str:
    """Stable format: os-owner-... / os-channel-... / os-guest-... (hex suffix)."""
    return f"os-{prefix}-{token_hex(16)}"


def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _load_raw_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _apply_updates(lines: list[str], updates: dict[str, str]) -> list[str]:
    """Replace KEY=value lines in place; preserve comments and blank lines."""
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(line)
    # Append any keys that were missing from file
    for k, v in updates.items():
        if k not in seen:
            if out and out[-1].strip():
                out.append("")
            out.append(f"{k}={v}")
    return out


def ensure_keys(*, dry_run: bool) -> dict[str, str]:
    if not ENV_PATH.exists():
        if EXAMPLE_PATH.exists():
            print(f"Creating {ENV_PATH} from .env.example …", file=sys.stderr)
            if not dry_run:
                ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
                ENV_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            raise SystemExit(f"Missing {ENV_PATH} and {EXAMPLE_PATH}")

    raw = _load_raw_lines(ENV_PATH)
    data = _parse_env("\n".join(raw))

    generated: dict[str, str] = {}

    def needs_fill(name: str) -> bool:
        v = data.get(name, "")
        if v in _PLACEHOLDERS:
            return True
        if name.startswith("API_KEY_") and v.startswith("your-"):
            return True
        return False

    mapping = {
        "API_KEY_OWNER": lambda: _gen_owner_like("owner"),
        "API_KEY_CHANNEL": lambda: _gen_owner_like("channel"),
        "API_KEY_GUEST": lambda: _gen_owner_like("guest"),
    }

    updates: dict[str, str] = {}
    for name in _KEY_NAMES:
        if name in mapping and needs_fill(name):
            new_val = mapping[name]()
            generated[name] = new_val
            updates[name] = new_val
            data[name] = new_val

    if not updates:
        print("API keys already set (non-placeholder). No changes.", file=sys.stderr)
        return {}

    if dry_run:
        print("[dry-run] Would generate:", file=sys.stderr)
        for k, v in updates.items():
            print(f"  {k}={v}", file=sys.stderr)
        return generated

    new_lines = _apply_updates(raw, updates)
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return generated


def main() -> None:
    p = argparse.ArgumentParser(description="Fill missing Agent OS API keys in config/.env")
    p.add_argument("--dry-run", action="store_true", help="Print what would be written, do not save")
    args = p.parse_args()

    generated = ensure_keys(dry_run=args.dry_run)
    if not generated and not args.dry_run:
        return

    if generated:
        print("")
        print("=" * 60)
        print("  Agent OS — 以下为新生成的 API 密钥（请立即保存）")
        print("=" * 60)
        for k in _KEY_NAMES:
            if k in generated:
                print(f"  {k}={generated[k]}")
        print("")
        print("  Web 控制台：把 API_KEY_OWNER 粘贴到页面顶部「API KEY」输入框。")
        print("  保存位置：", ENV_PATH)
        print("=" * 60)
        print("")
        print("若使用 Docker，请执行: docker compose restart agent-os", file=sys.stderr)


if __name__ == "__main__":
    main()
