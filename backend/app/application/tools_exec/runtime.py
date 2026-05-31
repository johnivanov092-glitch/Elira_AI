from __future__ import annotations

import re

from app.application.run_history.service import RunHistoryService
from app.domain.runtime.python_runner import execute_python


def run_python(code: str):
    return execute_python(code)


def analyze_code(code: str, language: str = "python", filename: str = ""):
    source = code or ""
    lines = source.split("\n")
    lang = language.lower()

    analysis = {
        "filename": filename,
        "language": lang,
        "total_lines": len(lines),
        "blank_lines": sum(1 for line in lines if not line.strip()),
        "comment_lines": 0,
        "functions": [],
        "classes": [],
        "imports": [],
    }

    if lang in ("python", "py"):
        for index, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                analysis["comment_lines"] += 1
            fn_match = re.match(r"^def\s+(\w+)", stripped)
            if fn_match:
                analysis["functions"].append({"name": fn_match.group(1), "line": index})
            class_match = re.match(r"^class\s+(\w+)", stripped)
            if class_match:
                analysis["classes"].append({"name": class_match.group(1), "line": index})
            if stripped.startswith("import ") or stripped.startswith("from "):
                analysis["imports"].append({"text": stripped, "line": index})

    elif lang in ("javascript", "js", "jsx", "typescript", "ts", "tsx"):
        for index, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//"):
                analysis["comment_lines"] += 1
            fn_match = re.match(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", stripped)
            if fn_match:
                analysis["functions"].append({"name": fn_match.group(1), "line": index})
            class_match = re.match(r"(?:export\s+)?class\s+(\w+)", stripped)
            if class_match:
                analysis["classes"].append({"name": class_match.group(1), "line": index})
            if stripped.startswith("import ") or stripped.startswith("const ") and "require(" in stripped:
                analysis["imports"].append({"text": stripped[:80], "line": index})

    analysis["code_lines"] = (
        analysis["total_lines"] - analysis["blank_lines"] - analysis["comment_lines"]
    )

    return {"ok": True, "analysis": analysis}


def get_run_history(limit: int = 50):
    try:
        service = RunHistoryService()
        runs = service.list_runs(limit=limit)
        return {"ok": True, "runs": list(reversed(runs)), "count": len(runs)}
    except Exception as exc:
        return {"ok": False, "runs": [], "error": str(exc)}
