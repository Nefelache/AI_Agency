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

Create `my_agent_os/config/.env` based on `my_agent_os/config/.env.example`.

Minimum required:

- `DEEPSEEK_API_KEY`
- `API_KEY_OWNER`, `API_KEY_CHANNEL`, `API_KEY_GUEST`
- `WHATSAPP_ALLOW_FROM`
- `WHATSAPP_BRIDGE_SECRET`

Create a Compose `.env` file in repo root for domain + bridge secret:

```bash
cat > .env <<'EOF'
DOMAIN=your.domain.com
WHATSAPP_BRIDGE_SECRET=your-random-secret
EOF
```

## 4. Start the stack

From repo root:

```bash
docker compose up -d --build
docker compose ps
```

## 5. Link WhatsApp (QR)

Tail the bridge logs and scan the printed QR code:

```bash
docker compose logs -f whatsapp-bridge
```

When linked, send a DM to that WhatsApp account. Messages will be routed to Agent OS.

## 6. Access the console

- `https://your.domain.com/` (static UI)
- `https://your.domain.com/docs` (FastAPI docs)

All API routes require API keys via `X-API-Key` or `Authorization: Bearer ...`.

