from __future__ import annotations

import io
import traceback
from contextlib import redirect_stdout, redirect_stderr
from typing import Any

SAFE_BUILTINS = {
    "print": print,
    "len": len,
    "range": range,
    "min": min,
    "max": max,
    "sum": sum,
    "sorted": sorted,
    "enumerate": enumerate,
    "zip": zip,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "abs": abs,
    "round": round,
}

ALLOWED_IMPORTS = {
    "json",
    "math",
    "statistics",
    "random",
    "datetime",
    "itertools",
    "collections",
    "re",
}


def _safe_import(name: str, globals=None, locals=None, fromlist=(), level=0):
    root = (name or "").split(".")[0]
    if root not in ALLOWED_IMPORTS:
        raise ImportError(f"Import blocked: {name}")
    return __import__(name, globals, locals, fromlist, level)


def execute_python(code: str) -> dict[str, Any]:
    code = (code or "").strip()
    if not code:
        return {"ok": False, "error": "Empty code"}

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    exec_globals = {
        "__builtins__": {
            **SAFE_BUILTINS,
            "__import__": _safe_import,
        }
    }
    exec_locals = {}

    try:
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            exec(code, exec_globals, exec_locals)

        stdout_text = stdout_buffer.getvalue()
        stderr_text = stderr_buffer.getvalue()

        return {
            "ok": True,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "locals": {
                k: repr(v)
                for k, v in exec_locals.items()
                if not k.startswith("__")
            },
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "stdout": stdout_buffer.getvalue(),
            "stderr": stderr_buffer.getvalue(),
        }
