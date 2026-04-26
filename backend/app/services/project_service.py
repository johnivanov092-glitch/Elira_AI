"""Compatibility facade for project filesystem helpers."""
from __future__ import annotations

from app.infrastructure.storage.project_files import (
    BASE_DIR,
    list_project_tree,
    read_project_file,
    search_project,
    write_project_file,
)

__all__ = [
    "BASE_DIR",
    "list_project_tree",
    "read_project_file",
    "search_project",
    "write_project_file",
]
