from __future__ import annotations

from pathlib import Path

class SandboxError(Exception):
    """Compatibility exception retained for older callers."""


def _resolve_safe(project_root: Path, raw_path: str) -> Path:
    """Resolve a relative path from ``project_root`` or accept any absolute path."""
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    resolved = candidate.resolve()
    return resolved


# Canonical truncation lives in app.infrastructure.text; kept as _truncate_middle
# here for the existing call sites (_run, etc.) and tests.
from app.infrastructure.text import truncate_middle as _truncate_middle  # noqa: E402
