"""Thin facade — all library service logic lives in application/library/library_service.py."""
from app.application.library.library_service import (  # noqa: F401
    _conn,
    _read_disk_preview,
    build_library_context,
    delete_library_file,
    list_library_files,
    set_library_active,
)
