from __future__ import annotations

from pathlib import Path
from typing import Any


def tool_sandbox_run(
    project_root: Path,
    *,
    code: str,
    install: list[str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Execute Python code in an isolated per-project venv.

    Persistent: pip installs and any files the script writes to
    `./work/` survive between calls within the same project.
    Reset with `sandbox_reset`.
    """
    if not isinstance(code, str) or not code.strip():
        return {"ok": False, "error": "code_required", "text": "ERROR: code is empty"}
    from app.application.code_agent.sandbox import run_in_sandbox

    del timeout  # compatibility input; Workflow Stop owns termination
    result = run_in_sandbox(
        project_root,
        code=code,
        install=install,
    )

    parts: list[str] = []
    parts.append(f"[sandbox: {result['sandbox_path']}]")
    parts.append(f"source={result['source_path']} sha256={result['source_sha256']}")
    parts.append(f"execution={result['execution_id']} semantic_status=unverified")
    parts.append(f"exit={result['exit_code']}  took={result['took_seconds']}s")
    if result.get("error"):
        parts.append(f"ERROR: {result['error']}")
    if result.get("install_log"):
        parts.append(f"PIP:\n{result['install_log']}")
    if result.get("stdout"):
        parts.append(f"STDOUT:\n{result['stdout']}")
    if result.get("stderr"):
        parts.append(f"STDERR:\n{result['stderr']}")
    ok = result.get("ok") is True and result.get("exit_code") == 0
    return {
        **result,
        "ok": ok,
        "error": None if ok else "sandbox_failed",
        "text": "\n".join(parts),
    }


def tool_sandbox_reset(project_root: Path) -> dict[str, Any]:
    """Wipe the project's sandbox (venv + work dir). Next sandbox_run
    starts fresh."""
    from app.application.code_agent.sandbox import reset_sandbox

    result = reset_sandbox(project_root)
    if not result.get("ok"):
        return {
            "ok": False,
            "error": "sandbox_reset_failed",
            "text": f"ERROR: {result.get('error', 'reset failed')}",
        }
    if not result.get("existed"):
        return {"ok": True, "text": "Sandbox did not exist (nothing to reset)."}
    return {"ok": True, "text": f"Sandbox reset: {result['sandbox_path']}"}
