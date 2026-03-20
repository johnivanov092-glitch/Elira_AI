"""git_routes.py — Git API."""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.services.git_service import git_status, git_diff, git_log, git_commit, git_branches

router = APIRouter(prefix="/api/git", tags=["git"])

class GitCommitRequest(BaseModel):
    message: str
    repo_path: str = ""
    add_all: bool = True

class GitPathRequest(BaseModel):
    repo_path: str = ""
    file_path: str = ""

@router.get("/status")
def route_status(repo_path: str = ""):
    return git_status(repo_path or None)

@router.get("/log")
def route_log(repo_path: str = "", limit: int = 15):
    return git_log(repo_path or None, limit=limit)

@router.get("/branches")
def route_branches(repo_path: str = ""):
    return git_branches(repo_path or None)

@router.post("/diff")
def route_diff(payload: GitPathRequest):
    return git_diff(payload.repo_path or None, payload.file_path or None)

@router.post("/commit")
def route_commit(payload: GitCommitRequest):
    return git_commit(payload.message, payload.repo_path or None, payload.add_all)
