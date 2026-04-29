from __future__ import annotations

from app.application.project_patch.runtime import ProjectPatchRuntime
from app.services.project_service import BASE_DIR, read_project_file, write_project_file


class ProjectPatchService(ProjectPatchRuntime):
    """
    Backward-compatible patch service with rollback snapshots.

    Existing methods stay intact:
    - preview_patch
    - apply_patch
    - replace_in_file

    New capabilities:
    - backup snapshots before apply
    - hash verification
    - rollback by backup id
    - backup listing
    """

    def __init__(self) -> None:
        super().__init__(
            project_root=BASE_DIR,
            read_project_file_func=read_project_file,
            write_project_file_func=write_project_file,
        )
