"""Static code analysis — line/function/class/import counting for Python and JS/TS."""
from __future__ import annotations

import re


def analyze_code(code: str, language: str = "python", filename: str = "") -> dict:
    lines = (code or "").split("\n")
    lang = language.lower()

    result: dict = {
        "filename": filename,
        "language": lang,
        "total_lines": len(lines),
        "blank_lines": sum(1 for l in lines if not l.strip()),
        "comment_lines": 0,
        "functions": [],
        "classes": [],
        "imports": [],
    }

    if lang in ("python", "py"):
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if s.startswith("#"):
                result["comment_lines"] += 1
            m = re.match(r"^def\s+(\w+)", s)
            if m:
                result["functions"].append({"name": m.group(1), "line": i})
            m = re.match(r"^class\s+(\w+)", s)
            if m:
                result["classes"].append({"name": m.group(1), "line": i})
            if s.startswith("import ") or s.startswith("from "):
                result["imports"].append({"text": s, "line": i})

    elif lang in ("javascript", "js", "jsx", "typescript", "ts", "tsx"):
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if s.startswith("//"):
                result["comment_lines"] += 1
            m = re.match(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", s)
            if m:
                result["functions"].append({"name": m.group(1), "line": i})
            m = re.match(r"(?:export\s+)?class\s+(\w+)", s)
            if m:
                result["classes"].append({"name": m.group(1), "line": i})
            if s.startswith("import ") or (s.startswith("const ") and "require(" in s):
                result["imports"].append({"text": s[:80], "line": i})

    result["code_lines"] = result["total_lines"] - result["blank_lines"] - result["comment_lines"]
    return result
