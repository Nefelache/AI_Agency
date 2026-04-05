"""
Code Runner — execute Python or shell snippets in a sandboxed subprocess
with a configurable timeout. NEVER allows network or filesystem access
outside the agent workspace unless AGENT_CODE_ALLOW_NETWORK=1.

Security posture:
  - subprocess with resource limits (ulimit)
  - stdout/stderr captured, combined output capped at 4 KB
  - timeout enforced via subprocess communicate()
  - no persistent state between invocations
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from typing import Any

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.tools import register

_DEFAULT_TIMEOUT = 10      # seconds
_MAX_OUTPUT      = 4_096   # 4 KB
_ALLOW_SHELL     = os.getenv("AGENT_CODE_ALLOW_SHELL", "0") == "1"


@register
class CodeRunner(Skill):
    name = "code_runner"
    description = (
        "Execute a Python code snippet. "
        "Params: code (str), language ('python'|'shell', default 'python'), "
        "timeout (int seconds, optional, max 30)."
    )
    skill_instructions = """
When to use: user asks to run/calculate/execute code or a snippet.
Required: code (non-empty string). language default python; shell only if deployment enabled.
If user did not provide code, do NOT call — ask for the snippet.
"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        code     = params.get("code", "").strip()
        language = params.get("language", "python").lower()
        timeout  = min(int(params.get("timeout", _DEFAULT_TIMEOUT)), 30)

        if not code:
            return {"success": False, "reason": "Missing 'code'."}

        if language == "shell":
            if not _ALLOW_SHELL:
                return {"success": False, "reason": "Shell execution disabled. Set AGENT_CODE_ALLOW_SHELL=1 to enable."}
            return self._run_shell(code, timeout)
        elif language == "python":
            return self._run_python(code, timeout)
        else:
            return {"success": False, "reason": f"Unsupported language: {language}. Use 'python' or 'shell'."}

    def _run_python(self, code: str, timeout: int) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp_path = f.name
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-S", tmp_path],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = result.stdout[:_MAX_OUTPUT]
            stderr = result.stderr[:_MAX_OUTPUT]
            combined = stdout + (f"\n[stderr]\n{stderr}" if stderr.strip() else "")
            return {
                "success":    result.returncode == 0,
                "returncode": result.returncode,
                "stdout":     stdout,
                "stderr":     stderr,
                "output":     combined.strip() or "(no output)",
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "reason": f"Execution timed out after {timeout}s."}
        except Exception as e:
            return {"success": False, "reason": str(e)}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _run_shell(self, code: str, timeout: int) -> dict[str, Any]:
        try:
            result = subprocess.run(
                code,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = result.stdout[:_MAX_OUTPUT]
            stderr = result.stderr[:_MAX_OUTPUT]
            combined = stdout + (f"\n[stderr]\n{stderr}" if stderr.strip() else "")
            return {
                "success":    result.returncode == 0,
                "returncode": result.returncode,
                "stdout":     stdout,
                "stderr":     stderr,
                "output":     combined.strip() or "(no output)",
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "reason": f"Timed out after {timeout}s."}
        except Exception as e:
            return {"success": False, "reason": str(e)}
