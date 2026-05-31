from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.application.tools_exec import runtime as tools_exec_runtime

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
    return tools_exec_runtime.run_python(payload.code)


@router.post("/analyze-code")
def analyze_code(payload: AnalyzeRequest):
    return tools_exec_runtime.analyze_code(
        payload.code,
        language=payload.language,
        filename=payload.filename,
    )


@router.get("/run-history")
def get_run_history(limit: int = 50):
    return tools_exec_runtime.get_run_history(limit)
