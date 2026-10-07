"""Stable project identity helpers shared by project-scoped subsystems."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def normalize_project_path(project_root: Path | str) -> str:
    """Return a stable absolute path representation for identity hashing."""
    resolved = Path(project_root).expanduser().resolve()
    normalized = os.path.normcase(str(resolved)).replace("\\", "/")
    return normalized.rstrip("/") or "/"


def project_scope_id(project_root: Path | str) -> str:
    """Opaque identity for storage keys. Display names must stay separate."""
    normalized = normalize_project_path(project_root)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"scope:{digest}"


def legacy_project_key(project_root: Path | str) -> str:
    """Previous basename key, used only to remove ambiguous legacy RAG rows."""
    root = Path(project_root).expanduser().resolve()
    return root.name or str(root)
