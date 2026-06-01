"""Stable project identity helpers shared by project-scoped subsystems."""
from __future__ import annotations

import hashlib
import os
import re
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


def project_scope_slug(project_root: Path | str) -> str:
    """Filesystem-safe sandbox directory name with a collision-resistant suffix."""
    root = Path(project_root).expanduser().resolve()
    label = re.sub(r"[^a-z0-9_-]+", "_", (root.name or "project").strip().lower()).strip("_")
    digest = project_scope_id(root).removeprefix("scope:")[:16]
    return f"{label or 'project'}-{digest}"


def legacy_project_key(project_root: Path | str) -> str:
    """Previous basename key, used only to remove ambiguous legacy RAG rows."""
    root = Path(project_root).expanduser().resolve()
    return root.name or str(root)
