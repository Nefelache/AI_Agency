"""
Compatibility shim — canonical stress test lives at repo root: stress/stress_test.py

Run on server after git pull:
  ./stress/run.sh
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "stress" / "stress_test.py"


if __name__ == "__main__":
    if not _SCRIPT.is_file():
        print(f"Missing {_SCRIPT}", file=sys.stderr)
        sys.exit(1)
    sys.exit(subprocess.call([sys.executable, str(_SCRIPT)] + sys.argv[1:]))
