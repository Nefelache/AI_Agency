#!/usr/bin/env bash
set -euo pipefail

echo "== Agent OS DO bootstrap =="
echo "This script installs Docker + Compose plugin on Ubuntu."

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This script is intended for Ubuntu/Debian (apt-get required)."
  exit 1
fi

sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

sudo usermod -aG docker "$USER" || true

echo "Docker installed."
echo "Log out and back in (or reboot) to apply docker group membership."

