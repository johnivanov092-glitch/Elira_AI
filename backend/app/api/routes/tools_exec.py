"""
tools_exec.py — endpoints for Python execution, code analysis, and run history.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.infrastructure.runtime.python_runner import execute_python
from app.application.tools.code_analyzer import analyze_code
from app.infrastructure.db.run_history_service import RunHistoryService

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
    return execute_python(payload.code)


@router.post("/analyze-code")
def route_analyze_code(payload: AnalyzeRequest):
    analysis = analyze_code(payload.code, payload.language, payload.filename)
    return {"ok": True, "analysis": analysis}


_HISTORY = RunHistoryService()


@router.get("/run-history")
def get_run_history(limit: int = 50):
    try:
        runs = _HISTORY.list_runs(limit=limit)
        return {"ok": True, "runs": list(reversed(runs)), "count": len(runs)}
    except Exception as e:
        return {"ok": False, "runs": [], "error": str(e)}
