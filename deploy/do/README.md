# DigitalOcean Deployment (Docker Compose)

## Prereqs
- A DigitalOcean Droplet (Ubuntu LTS recommended, 2GB+ RAM).
- A domain name pointing to the Droplet public IP (A record).

## 1. Install Docker on the Droplet

SSH to the droplet, then run:

```bash
# Copy this repo to the droplet first, then:
cd /path/to/repo
chmod +x deploy/do/bootstrap.sh
./deploy/do/bootstrap.sh
```

Or copy `deploy/do/bootstrap.sh` onto the droplet and run it:

```bash
chmod +x bootstrap.sh
./bootstrap.sh
```

Re-login so your user can run docker without sudo.

## 2. Upload the project

Clone or upload this repository to the droplet (recommended: git).

## 3. Configure environment

**交互式配置（推荐）：**

```bash
cd /opt/agent-os
python3 setup.py
```

按提示输入 DeepSeek API Key、手机号等，回车可自动生成 API 密钥。  
或手动复制并编辑：`cp my_agent_os/config/.env.example my_agent_os/config/.env`

**重新部署后网页没有 Owner Key？** 若 `.env` 是从模板复制的，里面的 `API_KEY_OWNER=your-owner-key` 只是占位符，**不会**自动变成可用密钥。在服务器项目根目录执行（非交互，会生成并打印新密钥）：

```bash
python3 scripts/ensure_api_keys.py
docker compose restart agent-os
```

终端会打印 `API_KEY_OWNER=os-owner-...`，复制到浏览器顶部「API KEY」即可。

## 4. Start the stack

From repo root:

```bash
docker compose up -d --build
docker compose ps
```

默认会同时启动 `memory-maintenance` 服务（每 24 小时一次），自动把近期碎片化 episodic 记忆整理为 semantic 并清理低价值片段。

可选配置（写到根目录 `.env`）：

```bash
MEMORY_MAINTENANCE_INTERVAL_SECONDS=86400
MEMORY_MAINTENANCE_USER_ID=default
```

## 5. Link WhatsApp (QR)

Tail the bridge logs and scan the printed QR code:

```bash
docker compose logs -f whatsapp-bridge
```

When linked, send a DM to that WhatsApp account. Messages will be routed to Agent OS.

## 6. Access the console

- `https://your.domain.com/` (Agent OS chat terminal)
- `https://your.domain.com/openclaw/` (operations console — WebSocket + token; see [docs/CONTROL_CONSOLE.md](../docs/CONTROL_CONSOLE.md))
- `https://your.domain.com/docs` (FastAPI docs)

All API routes require API keys via `X-API-Key` or `Authorization: Bearer ...`.

