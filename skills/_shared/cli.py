"""Common JSON command-line boundary for mutable skill scenarios."""
from __future__ import annotations
import argparse
import importlib
import inspect
import json
import logging
import math
import os
from pathlib import Path
import sys

def bootstrap() -> None:
    configured = os.getenv("ELIRA_BACKEND_ROOT")
    candidates = [Path(configured)] if configured else [p / "backend" for p in Path(__file__).resolve().parents]
    backend = next((p for p in candidates if (p / "app/core/config.py").is_file()), None)
    if backend is None:
        raise RuntimeError("Run this skill from Elira, or set ELIRA_BACKEND_ROOT to its backend directory")
    sys.path.insert(0, str(backend.resolve()))
    from app.application.code_agent.tools._shell import set_current_run_id
    set_current_run_id(os.getenv("ELIRA_PARENT_RUN_ID", ""))
    os.environ.setdefault("ELIRA_SKILLS_ROOT", str(Path(__file__).resolve().parents[1]))

def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value

def main(actions: dict[str, str]) -> int:
    parser = argparse.ArgumentParser(description="Mutable skill scenarios; JSON arguments and result")
    parser.add_argument("action", choices=sorted(actions))
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path, help="UTF-8 JSON object with scenario arguments")
    source.add_argument("--args", default="{}", help="Inline JSON object")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, help="Save the same JSON result to a file")
    parsed = parser.parse_args()
    try:
        bootstrap()
        os.chdir(parsed.workspace.resolve())
        arguments = json.loads(parsed.input.read_text(encoding="utf-8") if parsed.input else parsed.args)
        if not isinstance(arguments, dict) or any(str(k).startswith("_") for k in arguments):
            raise ValueError("Arguments must be an object without private runtime keys")
        module_name, function_name = actions[parsed.action].split(":", 1)
        handler = getattr(importlib.import_module(module_name), function_name)
        if "project_root" in inspect.signature(handler).parameters:
            arguments["project_root"] = parsed.workspace.resolve()
        value = handler(**arguments)
        if isinstance(value, Path):
            value = {"ok": True, "path": str(value)}
        if not isinstance(value, dict):
            raise TypeError("Scenario did not return a JSON object")
        from app.core.redaction import redact_secrets
        result = json_safe(redact_secrets(value))
        encoded = json.dumps(result, ensure_ascii=False, default=str, allow_nan=False)
    except Exception as exc:
        try:
            from app.core.redaction import redact_text
            message = redact_text(str(exc))
        except ImportError:
            message = "Skill backend is unavailable; check ELIRA_BACKEND_ROOT"
        logging.getLogger(__name__).error("Skill scenario failed: %s: %s", type(exc).__name__, message)
        result = {"ok": False, "error": type(exc).__name__, "text": message}
        encoded = json.dumps(result, ensure_ascii=False)
    if parsed.output:
        parsed.output.parent.mkdir(parents=True, exist_ok=True)
        parsed.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 1 if result.get("ok") is False or result.get("status") in {"failed", "unverified"} else 0
