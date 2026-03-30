#!/usr/bin/env python3
"""
Post-process the built Control UI bundle: Agent OS chrome only.

Does NOT replace internal keys like includeInOpenClawGroup or group:openclaw strings
that appear in compiled feature metadata (would break runtime).
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENCLAW_DIR = ROOT / "my_agent_os" / "api_gateway" / "static" / "openclaw"
SOURCE_ICON = ROOT / "my_agent_os" / "api_gateway" / "static" / "icon-192.svg"
BRANDING_DIR = OPENCLAW_DIR / "branding"
ICON_DST = BRANDING_DIR / "icon.svg"


def patch_bundle(path: Path) -> int:
    t = path.read_text(encoding="utf-8")
    orig = t

    pairs: list[tuple[str, str]] = [
        ('alt="OpenClaw"', 'alt="Agent OS"'),
        ('<div class="login-gate__title">OpenClaw</div>', '<div class="login-gate__title">Agent OS</div>'),
        (
            '<span class="sidebar-brand__title">OpenClaw</span>',
            '<span class="sidebar-brand__title">Agent OS</span>',
        ),
        ("\n            OpenClaw\n          </span>", "\n            Agent OS\n          </span>"),
        ("Show or set OpenClaw MCP servers.", "Show or set MCP servers."),
        ("Restart OpenClaw.", "Restart Agent OS gateway."),
    ]
    for a, b in pairs:
        t = t.replace(a, b)

    if t != orig:
        path.write_text(t, encoding="utf-8")
    return orig.count("OpenClaw") - t.count("OpenClaw")


def patch_index_html(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    raw = re.sub(
        r"<title>[^<]*</title>",
        "<title>Agent OS — Operations Console</title>",
        raw,
        count=1,
    )
    # Drop duplicate PNG favicon; single SVG mark for Agent OS (green compass mark)
    raw = raw.replace(
        '    <link rel="icon" type="image/svg+xml" href="/openclaw/favicon.svg" />\n',
        '    <link rel="icon" type="image/svg+xml" href="/openclaw/branding/icon.svg" />\n',
    )
    raw = raw.replace(
        '    <link rel="icon" type="image/png" sizes="32x32" href="/openclaw/favicon-32.png" />\n',
        "",
    )
    raw = raw.replace(
        '    <link rel="apple-touch-icon" sizes="180x180" href="/openclaw/apple-touch-icon.png" />\n',
        '    <link rel="apple-touch-icon" sizes="180x180" href="/openclaw/branding/icon.svg" />\n',
    )
    path.write_text(raw, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dir",
        type=Path,
        default=OPENCLAW_DIR,
        help="OpenClaw static output directory",
    )
    args = ap.parse_args()
    base: Path = args.dir

    if not base.is_dir():
        print(f"Missing directory: {base}")
        return 1

    BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    if SOURCE_ICON.is_file():
        shutil.copyfile(SOURCE_ICON, ICON_DST)
    else:
        print(f"Warning: source icon not found: {SOURCE_ICON}")

    index = base / "index.html"
    if not index.is_file():
        print(f"Missing {index}")
        return 1

    m = re.search(r'/openclaw/assets/(index-[^"]+\.js)"', index.read_text(encoding="utf-8"))
    if not m:
        print("Could not find main bundle in index.html")
        return 1
    bundle = base / "assets" / m.group(1)
    if not bundle.is_file():
        print(f"Missing bundle {bundle}")
        return 1

    n = patch_bundle(bundle)
    patch_index_html(index)
    print(f"Rebranded UI: {bundle.name} (visible OpenClaw strings removed: {n})")
    print(f"Branding icon: {ICON_DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
