#!/usr/bin/env bash
# Run stress test from repo root. Safe to use on a server after: git pull && pip install -r requirements.txt
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
elif [[ -n "${PYTHON:-}" ]]; then
  :
else
  PYTHON="python3"
fi

exec "$PYTHON" "${ROOT}/stress/stress_test.py" "$@"
