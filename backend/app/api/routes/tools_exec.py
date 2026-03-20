"""
tools.py route — эндпоинты для Python exec и анализа кода.
Подключается в main.py.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.services.python_runner import execute_python

router = APIRouter(prefix="/api/tools", tags=["tools-exec"])


class PythonExecRequest(BaseModel):
    code: str
    timeout: int = 10


class AnalyzeRequest(BaseModel):
    code: str
    language: str = "python"
    filename: str = ""


@router.post("/run-python")
def run_python(payload: PythonExecRequest):
    """Выполняет Python-код в sandbox и возвращает stdout/stderr."""
    result = execute_python(payload.code)
    return result


@router.post("/analyze-code")
def analyze_code(payload: AnalyzeRequest):
    """Базовый анализ кода: считает строки, функции, классы, импорты."""
    code = payload.code or ""
    lines = code.split("\n")
    lang = payload.language.lower()

    analysis = {
        "filename": payload.filename,
        "language": lang,
        "total_lines": len(lines),
        "blank_lines": sum(1 for l in lines if not l.strip()),
        "comment_lines": 0,
        "functions": [],
        "classes": [],
        "imports": [],
    }

    if lang in ("python", "py"):
        import re
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                analysis["comment_lines"] += 1
            if re.match(r"^def\s+(\w+)", stripped):
                analysis["functions"].append({"name": re.match(r"^def\s+(\w+)", stripped).group(1), "line": i})
            if re.match(r"^class\s+(\w+)", stripped):
                analysis["classes"].append({"name": re.match(r"^class\s+(\w+)", stripped).group(1), "line": i})
            if stripped.startswith("import ") or stripped.startswith("from "):
                analysis["imports"].append({"text": stripped, "line": i})

    elif lang in ("javascript", "js", "jsx", "typescript", "ts", "tsx"):
        import re
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//"):
                analysis["comment_lines"] += 1
            fn = re.match(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", stripped)
            if fn:
                analysis["functions"].append({"name": fn.group(1), "line": i})
            cls = re.match(r"(?:export\s+)?class\s+(\w+)", stripped)
            if cls:
                analysis["classes"].append({"name": cls.group(1), "line": i})
            if stripped.startswith("import ") or stripped.startswith("const ") and "require(" in stripped:
                analysis["imports"].append({"text": stripped[:80], "line": i})

    analysis["code_lines"] = analysis["total_lines"] - analysis["blank_lines"] - analysis["comment_lines"]

    return {"ok": True, "analysis": analysis}


@router.get("/run-history")
def get_run_history(limit: int = 50):
    """История запусков агента — последние N записей."""
    try:
        from app.services.run_history_service import RunHistoryService
        svc = RunHistoryService()
        runs = svc.list_runs(limit=limit)
        return {"ok": True, "runs": list(reversed(runs)), "count": len(runs)}
    except Exception as e:
        return {"ok": False, "runs": [], "error": str(e)}
