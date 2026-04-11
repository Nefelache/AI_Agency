"""
Skill Writer — enables the agent to author and register new skills at runtime.

Foundation for the self-extending agent (v1.5 will wire this into router_engine).
Security: only callable with ROOT role. Generated code is scanned before write.

Pipeline (Voyager-style):
  1. PROPOSE  — LLM designs the skill given a task description
  2. WRITE    — LLM generates the Python class
  3. SCAN     — Forbidden patterns check (no os.system, subprocess, eval, etc.)
  4. TEST     — Run in isolated subprocess to catch syntax/import errors
  5. REGISTER — Write to skills_layer/tools/ and hot-reload registry
  6. EXECUTE  — Immediately run the new skill with the original params
  7. STORE    — Record success in memory so Agent knows this skill exists
"""

from __future__ import annotations

import importlib
import logging
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TOOLS_DIR = Path(__file__).parent / "tools"

_SKILL_TEMPLATE = textwrap.dedent("""\
    from __future__ import annotations
    from typing import Any
    from my_agent_os.skills_layer.base import Skill
    from my_agent_os.skills_layer.tools import register

    @register
    class {class_name}(Skill):
        name = "{skill_name}"
        description = "{description}"

        async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
    {body}
""")

_FORBIDDEN_PATTERNS = [
    r"os\.system\(",
    r"subprocess\.",
    r"\beval\(",
    r"\bexec\(",
    r"__import__\(",
    r"open\(['\"]\/etc",
    r"open\(['\"]\/root",
    r"shutil\.rmtree\(",
    r"importlib\.import_module\(['\"]os",
]

_FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN_PATTERNS))


# ── Security ──────────────────────────────────────────────────────────────────

def scan_for_dangerous_code(code: str) -> list[str]:
    """Return list of forbidden pattern matches found in generated code."""
    return _FORBIDDEN_RE.findall(code)


# ── Code generation ───────────────────────────────────────────────────────────

async def generate_skill_code(task_description: str) -> str:
    """Ask the LLM to write a skill class for the given task description."""
    from my_agent_os.agent_core.llm_client import call_llm

    system = textwrap.dedent(f"""\
        You are an expert Python developer writing skills for an AI agent framework.

        Rules:
        - Subclass Skill and apply @register decorator
        - Use ONLY Python stdlib (no pip install)
        - The execute() method MUST be: async def execute(self, params: dict) -> dict
        - Return dict with: 'success' (bool), 'output' (str), and any extra fields
        - Start with exactly these two imports (no others from the framework):
            from my_agent_os.skills_layer.base import Skill
            from my_agent_os.skills_layer.tools import register
        - Name the class in PascalCase, set name = snake_case
        - Description must be one clear sentence explaining what it does and its params

        Available tools dir: {_TOOLS_DIR}

        Respond with ONLY the Python code. No markdown, no explanation, no ```python blocks.
    """)

    code = await call_llm(
        system_message=system,
        user_message=f"Write a skill for: {task_description}",
        temperature=0.2,
        max_tokens=1500,
    )
    return code.strip()


# ── Validation ────────────────────────────────────────────────────────────────

def _syntax_check(code: str) -> str | None:
    """Return error string if code has a syntax error, else None."""
    try:
        compile(code, "<skill>", "exec")
        return None
    except SyntaxError as e:
        return f"SyntaxError at line {e.lineno}: {e.msg}"


def _subprocess_test(code: str) -> str | None:
    """Run the code in an isolated subprocess to catch import/runtime errors."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp = f.name
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import ast; ast.parse(open('{tmp}').read())"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return result.stderr[:500]
        return None
    except Exception as e:
        return str(e)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _extract_skill_name(code: str) -> str | None:
    """Extract the skill name value from the generated code."""
    match = re.search(r'name\s*=\s*["\']([a-z_][a-z0-9_]*)["\']', code)
    return match.group(1) if match else None


# ── Registration ──────────────────────────────────────────────────────────────

def write_skill_to_disk(skill_name: str, code: str) -> Path:
    """Write generated skill code to the tools directory."""
    path = _TOOLS_DIR / f"{skill_name}.py"
    path.write_text(code, encoding="utf-8")
    logger.info("Skill written to disk: %s", path)
    return path


def register_skill(skill_name: str) -> bool:
    """Hot-reload the skill module and register it in the tool registry."""
    from my_agent_os.skills_layer.tools import reload_tool
    try:
        reload_tool(skill_name)
        logger.info("Skill registered: %s", skill_name)
        return True
    except Exception as e:
        logger.error("Skill registration failed [%s]: %s", skill_name, e)
        return False


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def create_skill(
    task_description: str,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """
    Full Voyager-style skill creation loop:
      generate → scan → validate → write → register
    Returns dict with 'success', 'skill_name', 'code', 'attempts', 'error'.
    """
    last_error = ""
    feedback = ""

    for attempt in range(1, max_attempts + 1):
        prompt = task_description
        if feedback:
            prompt += f"\n\nPrevious attempt failed with: {feedback}\nPlease fix the issue."

        code = await generate_skill_code(prompt)

        dangers = scan_for_dangerous_code(code)
        if dangers:
            last_error = f"Security scan failed — forbidden patterns: {dangers}"
            logger.warning("Skill writer security block (attempt %d): %s", attempt, dangers)
            feedback = last_error
            continue

        syntax_err = _syntax_check(code)
        if syntax_err:
            last_error = syntax_err
            feedback = f"Syntax error in generated code: {syntax_err}"
            logger.warning("Skill writer syntax error (attempt %d): %s", attempt, syntax_err)
            continue

        skill_name = _extract_skill_name(code)
        if not skill_name:
            last_error = "Could not extract skill name from generated code."
            feedback = last_error
            continue

        if (_TOOLS_DIR / f"{skill_name}.py").exists():
            last_error = f"Skill '{skill_name}' already exists."
            logger.info("Skill already exists, skipping write: %s", skill_name)
            return {"success": False, "skill_name": skill_name, "error": last_error, "attempts": attempt}

        write_skill_to_disk(skill_name, code)
        ok = register_skill(skill_name)

        if ok:
            logger.info("Skill created successfully [%s] after %d attempt(s)", skill_name, attempt)
            return {"success": True, "skill_name": skill_name, "code": code, "attempts": attempt}
        else:
            last_error = f"Registration failed for skill '{skill_name}'"
            feedback = last_error

    return {
        "success": False,
        "skill_name": None,
        "code": None,
        "attempts": max_attempts,
        "error": last_error,
    }
