from __future__ import annotations

import os
from pathlib import Path

# Owner-only escape hatch. Elira is a private, single-owner LOCAL tool. Confining
# every file tool to the opened project root stops the owner's OWN agent from
# reading things like ~/.ssh or scanning the home dir — tasks the owner explicitly
# wants (and that Claude itself can do on the same machine). Set
# ELIRA_FS_UNRESTRICTED=1 (e.g. in Elira.bat / backend/.env.local) to let file
# tools resolve paths anywhere on THIS machine. Default OFF keeps the containment
# boundary intact — nothing opens until the owner consciously flips this.
_TRUTHY = {"1", "true", "yes", "on"}


def _fs_unrestricted() -> bool:
    return os.getenv("ELIRA_FS_UNRESTRICTED", "").strip().lower() in _TRUTHY


class SandboxError(Exception):
    """Raised when a tool tries to access a path outside the project root."""


def _resolve_safe(project_root: Path, raw_path: str) -> Path:
    """Resolve `raw_path` (absolute or relative to project_root) and confirm
    it stays inside project_root. Raises SandboxError otherwise — unless the
    owner opted into whole-machine access via ELIRA_FS_UNRESTRICTED.
    """
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    resolved = candidate.resolve()
    root_resolved = project_root.resolve()
    if _fs_unrestricted():
        # Owner opted into full local filesystem access — resolve anywhere.
        return resolved
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SandboxError(
            f"Path '{raw_path}' resolves to {resolved}, which is outside the "
            f"project root {root_resolved}"
        ) from exc
    return resolved


# Canonical truncation lives in app.infrastructure.text; kept as _truncate_middle
# here for the existing call sites (_run, etc.) and tests.
from app.infrastructure.text import truncate_middle as _truncate_middle  # noqa: E402
