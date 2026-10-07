"""Skills extra compatibility facade.

Public surface re-exported from ``app.application.skills_extra.runtime`` for
API routes and the code-agent tool wrappers.
"""
from __future__ import annotations

from app.application.skills_extra.runtime import (
    BACKEND_UPLOADS,
    OUTPUT_DIR,
    WORKSPACE,
    analyze_csv,
)

__all__ = [
    "BACKEND_UPLOADS",
    "OUTPUT_DIR",
    "WORKSPACE",
    "analyze_csv",
]
