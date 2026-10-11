"""math: one call into the mutable MATH skill engine (owner decision 2026-10-11).

The tool owns no formulas. It writes the request JSON and runs the installed
skill's calculate.py, exactly what the model otherwise did by hand with
write_file + run_bash. Operations and their code stay editable in the skill.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.application.code_agent.tools._run import _agent_child_env
from app.core.skill_modules import skill_root

_TIMEOUT_SECONDS = 60
_MAX_REQUEST_BYTES = 2_000_000
_EXAMPLE = '{"operation":"evaluate","expression":"125000*16/100"}'


def _error(code: str, text: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "text": f"ERROR: {text}"}


def _skill_python(root: Path) -> str:
    venv = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # Without the skill's own environment the application interpreter runs it:
    # finance and arithmetic need only the standard library, algebra uses SymPy.
    return str(venv) if venv.is_file() else sys.executable


def math_request(request: Any = None, **flat: Any) -> dict[str, Any] | str:
    """Normalize the model's arguments to one MATH request, or return the error text."""
    payload = request if request is not None else (flat or None)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            return f"request is not valid JSON ({exc.msg}); pass an object like {_EXAMPLE}"
    if not isinstance(payload, dict) or not str(payload.get("operation") or "").strip():
        return f"request needs an object with operation, e.g. {_EXAMPLE}; operations: skill math SKILL.md"
    return payload


def tool_math(project_root: Path, *, request: Any = None, **flat: Any) -> dict[str, Any]:
    payload = math_request(request, **flat)
    if isinstance(payload, str):
        return _error("invalid_request", payload)
    root = skill_root("math")
    script = root / "calculate.py"
    if not script.is_file():
        return _error("skill_missing", f"MATH skill is not installed: {script} not found")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(data) > _MAX_REQUEST_BYTES:
        return _error("invalid_request", "request JSON is larger than 2 MB")
    with tempfile.TemporaryDirectory(prefix="elira-math-") as folder:
        source = Path(folder) / "request.json"
        source.write_bytes(data)
        try:
            completed = subprocess.run(
                [_skill_python(root), str(script), "--input", str(source)],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=_TIMEOUT_SECONDS,
                env=_agent_child_env(),
                # A relative plot path lands in the run workspace, not in the skill.
                cwd=str(project_root),
            )
        except subprocess.TimeoutExpired:
            return _error("timeout", f"MATH did not finish in {_TIMEOUT_SECONDS} s; simplify the calculation model")
    stdout = completed.stdout.decode("utf-8", "replace").strip()
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        result = None
    if not isinstance(result, dict):
        detail = (completed.stderr.decode("utf-8", "replace").strip() or stdout)[-2000:]
        return _error("skill_failed", f"MATH exit={completed.returncode}: {detail}")
    text = str(result.get("text") or "")
    if isinstance(result.get("explanation"), dict):
        text += "\nexplanation: " + json.dumps(result["explanation"], ensure_ascii=False)
    return {**result, "ok": result.get("ok") is True and completed.returncode == 0, "text": text}
