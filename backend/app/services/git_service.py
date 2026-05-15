"""Thin facade — all git service logic lives in infrastructure/vcs/git_service.py."""
from app.infrastructure.vcs.git_service import (  # noqa: F401
    _find_repo,
    _run,
    format_git_context,
    git_branches,
    git_commit,
    git_diff,
    git_log,
    git_status,
)
