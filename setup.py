#!/usr/bin/env python3
"""
Agent OS — 交互式配置向导

在项目根目录运行：
    python setup.py

会依次询问各项配置，回车可自动生成随机密钥。
支持本地和 Droplet 部署。
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from secrets import token_urlsafe

ROOT = Path(__file__).resolve().parent
CONFIG_ENV = ROOT / "my_agent_os" / "config" / ".env"
COMPOSE_ENV = ROOT / ".env"
BRIDGE_DIR = ROOT / "channels" / "whatsapp-bridge"


def _prompt(msg: str, default: str = "", secret: bool = False) -> str:
    if default:
        hint = f" [回车=使用默认/自动生成]"
    else:
        hint = ""
    val = input(f"{msg}{hint}\n> ").strip()
    if not val and default:
        return default
    return val


def _gen_key() -> str:
    return token_urlsafe(24)


def _load_existing() -> dict[str, str]:
    out: dict[str, str] = {}
    if not CONFIG_ENV.exists():
        return out
    for line in CONFIG_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _write_config(cfg: dict[str, str]) -> None:
    CONFIG_ENV.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Agent OS — 由 setup.py 生成",
        "",
        "# --- LLM (DeepSeek) ---",
        f"DEEPSEEK_API_KEY={cfg.get('DEEPSEEK_API_KEY', '')}",
        "",
        "# --- Auth ---",
        f"API_KEY_OWNER={cfg.get('API_KEY_OWNER', '')}",
        f"API_KEY_CHANNEL={cfg.get('API_KEY_CHANNEL', '')}",
        f"API_KEY_GUEST={cfg.get('API_KEY_GUEST', '')}",
        "",
        "# --- WhatsApp ---",
        f"WHATSAPP_ALLOW_FROM={cfg.get('WHATSAPP_ALLOW_FROM', '')}",
        f"WHATSAPP_BRIDGE_SECRET={cfg.get('WHATSAPP_BRIDGE_SECRET', '')}",
        "",
    ]
    CONFIG_ENV.write_text("\n".join(lines), encoding="utf-8")  # noqa: S103
    print(f"  已写入 {CONFIG_ENV}")


def _write_compose_env(bridge_secret: str) -> None:
    content = f"""DOMAIN=localhost
WHATSAPP_BRIDGE_SECRET={bridge_secret}
"""
    COMPOSE_ENV.write_text(content)
    print(f"  已写入 {COMPOSE_ENV}")


def run_setup() -> dict[str, str]:
    existing = _load_existing()

    print("\n=== Agent OS 配置向导 ===\n")

    # DeepSeek
    cfg = dict(existing)
    cfg["DEEPSEEK_API_KEY"] = _prompt(
        "请输入 DeepSeek API Key（留空则跳过，后续可手动配置）：",
        default=existing.get("DEEPSEEK_API_KEY", ""),
    )

    # API Keys
    for key in ["API_KEY_OWNER", "API_KEY_CHANNEL", "API_KEY_GUEST"]:
        current = existing.get(key) or _gen_key()
        val = _prompt(f"{key}（回车=自动生成）：", default=current)
        cfg[key] = val or _gen_key()

    # WhatsApp
    cfg["WHATSAPP_ALLOW_FROM"] = _prompt(
        "请输入允许的 WhatsApp 手机号 (E.164，如 +8613800138000)：",
        default=existing.get("WHATSAPP_ALLOW_FROM", ""),
    )
    bridge_secret = existing.get("WHATSAPP_BRIDGE_SECRET") or _gen_key()
    val = _prompt("WHATSAPP_BRIDGE_SECRET（回车=自动生成）：", default=bridge_secret)
    cfg["WHATSAPP_BRIDGE_SECRET"] = val or _gen_key()

    _write_config(cfg)
    _write_compose_env(cfg["WHATSAPP_BRIDGE_SECRET"])

    return cfg


def main() -> None:
    run_setup()

    print("\n--- 配置完成 ---\n")

    choice = _prompt("是否现在启动 Docker 服务？(y/N)：", default="")

    if choice.lower() == "y":
        # 若已安装 Docker，直接启动
        if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0:
            print("正在启动 agent-os + whatsapp-bridge...")
            subprocess.run(
                ["docker", "compose", "up", "-d", "--build", "agent-os", "whatsapp-bridge"],
                cwd=ROOT,
            )
            print("\n已启动。扫码链接 WhatsApp：")
            print("  docker compose logs -f whatsapp-bridge")
        else:
            bootstrap = ROOT / "deploy" / "do" / "bootstrap.sh"
            if bootstrap.exists() and platform.system() == "Linux":
                subprocess.run(["bash", str(bootstrap)], cwd=ROOT)
                print("\nDocker 安装完成。请退出 SSH 重新登录后执行：")
                print("  cd /opt/agent-os && python setup.py  # 再次运行，选择启动服务")
            else:
                print("请先安装 Docker，然后执行：")
                print("  docker compose up -d --build agent-os whatsapp-bridge")
        return

    print("下一步：")
    print("  1. 本地：python run.py")
    print("  2. Docker：docker compose up -d --build agent-os whatsapp-bridge")
    print("  3. 扫码：docker compose logs -f whatsapp-bridge")


if __name__ == "__main__":
    main()
