"""Project Brain service — compatibility shim.

All logic lives in ``app.application.project_brain.runtime``.
Public API re-exported for all callers.

Bug fix: the original file imported a non-existent ``GitService`` class
from ``git_service``.  The runtime now uses ``git_commit()`` directly.
"""
from __future__ import annotations

from app.application.project_brain.runtime import (
    ProjectBrainService,
    apply_patch,
    apply_patch_and_push,
    find_code,
    preview_patch,
    read_file,
    scan_project,
)

__all__ = [
    "ProjectBrainService",
    "apply_patch",
    "apply_patch_and_push",
    "find_code",
    "preview_patch",
    "read_file",
    "scan_project",
]
