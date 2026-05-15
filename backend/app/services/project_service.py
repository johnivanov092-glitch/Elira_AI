"""Thin facade — all project file logic lives in infrastructure/files/project_service.py."""
from app.infrastructure.files.project_service import (  # noqa: F401
    BASE_DIR,
    IGNORE_DIRS,
    TEXT_EXTS,
    _is_safe_path,
    _normalize_rel_path,
    list_project_tree,
    read_project_file,
    search_project,
    write_project_file,
)
