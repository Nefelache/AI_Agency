"""Interactive WhatsApp linker — OpenClaw-style QR flow.

Run this from the project root:

    python link_whatsapp.py

It will:
  1. Help you configure WhatsApp-related .env values
  2. Optionally start the Node.js Baileys bridge so you can scan the QR code

Backend (Agent OS FastAPI) should already be running separately (e.g. via `python run.py`).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from secrets import token_urlsafe


ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / "my_agent_os" / "config" / ".env"
BRIDGE_DIR = ROOT_DIR / "channels" / "whatsapp-bridge"


def _ensure_env_file() -> None:
    if ENV_PATH.exists():
        return
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("", encoding="utf-8")


def _has_key(content: str, key: str) -> bool:
    return any(line.strip().startswith(f"{key}=") for line in content.splitlines())


def configure_whatsapp_env() -> None:
    """Ensure WHATSAPP_ALLOW_FROM and WHATSAPP_BRIDGE_SECRET exist in .env."""
    _ensure_env_file()
    content = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    lines = content.splitlines()

    if not _has_key(content, "WHATSAPP_ALLOW_FROM"):
        phone = input("请输入你的手机号 (E.164 格式，例如 +8613800138000)：\n> ").strip()
        if phone:
            lines.append(f"WHATSAPP_ALLOW_FROM={phone}")

    generated_secret = None
    if not _has_key(content, "WHATSAPP_BRIDGE_SECRET"):
        generated_secret = token_urlsafe(32)
        lines.append(f"WHATSAPP_BRIDGE_SECRET={generated_secret}")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n已更新环境配置文件：{ENV_PATH}")
    if generated_secret:
        print("生成的 WHATSAPP_BRIDGE_SECRET 为：")
        print(generated_secret)
        print("请保存好该值，用于桥接服务认证。")


def start_bridge() -> None:
    """Run the Node.js WhatsApp bridge (npm install if needed)."""
    if not BRIDGE_DIR.exists():
        print(f"未找到桥接目录：{BRIDGE_DIR}")
        return

    env = os.environ.copy()
    # Load WHATSAPP_BRIDGE_SECRET from .env if present
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if not line or line.strip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k == "WHATSAPP_BRIDGE_SECRET":
                env.setdefault("AGENT_OS_SECRET", v)
    env.setdefault("AGENT_OS_URL", "http://127.0.0.1:8000")

    node_modules = BRIDGE_DIR / "node_modules"
    if not node_modules.exists():
        print("首次运行：正在为 WhatsApp bridge 执行 npm install...")
        proc = subprocess.run(["npm", "install"], cwd=BRIDGE_DIR)
        if proc.returncode != 0:
            print("npm install 失败，请检查 Node.js / npm 是否安装正确。")
            return

    print("\n启动 WhatsApp Baileys bridge，中途可用 Ctrl+C 停止。\n")
    print("请在终端看到二维码后，用手机 WhatsApp 扫码进行链接。\n")
    subprocess.run(["npm", "start"], cwd=BRIDGE_DIR, env=env)


def main() -> None:
    print("=== Agent OS — WhatsApp Linker ===")
    print("此向导会帮助你配置 .env 并启动扫码桥接。\n")

    configure_whatsapp_env()

    choice = input("\n是否现在启动 WhatsApp 桥接并显示二维码？ (y/N): ").strip().lower()
    if choice == "y":
        start_bridge()
    else:
        print("已完成配置。你可以稍后手动运行：")
        print("  cd channels/whatsapp-bridge && npm start")


if __name__ == "__main__":
    main()

