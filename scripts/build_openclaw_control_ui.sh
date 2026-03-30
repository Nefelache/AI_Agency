#!/usr/bin/env bash
# Build upstream OpenClaw Control UI and copy into Agent OS static dir.
# Requires: pnpm, Node (OpenClaw prefers Node 22+; Node 20 often works for UI-only build).
#
# The OpenClaw repo is not committed (gitignored under third_party/). Clone once:
#   git clone https://github.com/openclaw/openclaw.git third_party/openclaw
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR_UI="$ROOT/third_party/openclaw/ui"
DEST="$ROOT/my_agent_os/api_gateway/static/openclaw"

if [[ ! -f "$VENDOR_UI/package.json" ]]; then
  echo "Missing $VENDOR_UI — clone OpenClaw: git clone https://github.com/openclaw/openclaw.git third_party/openclaw" >&2
  exit 1
fi

export OPENCLAW_CONTROL_UI_BASE_PATH=/openclaw/
cd "$VENDOR_UI"
pnpm install
pnpm run build

rsync -a --delete "$ROOT/third_party/openclaw/dist/control-ui/" "$DEST/"
echo "Control UI -> $DEST ($(du -sh "$DEST" | cut -f1))"
