#!/usr/bin/env bash
# One-shot deploy script for DigitalOcean Droplet.
# Run as root: bash deploy.sh <GITHUB_REPO_URL>
# Example: bash deploy.sh https://github.com/yourname/agent-os.git

set -euo pipefail

REPO_URL="${1:-}"
if [[ -z "$REPO_URL" ]]; then
  echo "Usage: bash deploy.sh <GITHUB_REPO_URL>"
  echo "Example: bash deploy.sh https://github.com/yourname/agent-os.git"
  exit 1
fi

echo "== Agent OS Deploy =="
echo "Repo: $REPO_URL"

# Install git if needed
command -v git >/dev/null 2>&1 || apt-get update -y && apt-get install -y git

# Install Docker if needed
if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg lsb-release
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# Clone or pull
TARGET="/opt/agent-os"
if [[ -d "$TARGET/.git" ]]; then
  echo "Pulling updates..."
  cd "$TARGET"
  git pull
else
  echo "Cloning..."
  mkdir -p "$(dirname "$TARGET")"
  rm -rf "$TARGET"
  git clone "$REPO_URL" "$TARGET"
  cd "$TARGET"
fi

# Ensure config exists (run interactive setup if missing)
if [[ ! -f my_agent_os/config/.env ]]; then
  echo "未找到配置文件，启动交互式配置..."
  python3 setup.py || {
    echo "或手动复制: cp my_agent_os/config/.env.example my_agent_os/config/.env"
    exit 1
  }
fi

# Compose .env
if [[ ! -f .env ]]; then
  SECRET=$(grep WHATSAPP_BRIDGE_SECRET my_agent_os/config/.env 2>/dev/null | cut -d= -f2)
  cat > .env <<EOF
DOMAIN=localhost
WHATSAPP_BRIDGE_SECRET=${SECRET:-changeme}
EOF
  echo "Created .env from config"
fi

# Start (agent-os + whatsapp-bridge only, no Caddy - safe for existing sites)
echo "Starting agent-os + whatsapp-bridge..."
docker compose up -d --build agent-os whatsapp-bridge

echo ""
echo "Done. Check status: docker compose ps"
echo "Health: curl http://localhost:8000/health"
echo "Bridge logs (scan QR): docker compose logs -f whatsapp-bridge"
