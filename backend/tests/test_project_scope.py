from __future__ import annotations

import sys
import tempfile
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.projects.scope import (  # noqa: E402
    legacy_project_key,
    normalize_project_path,
    project_scope_id,
    project_scope_slug,
)


def test_scope_id_is_stable_for_same_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        root.mkdir()
        assert project_scope_id(root) == project_scope_id(str(root))
        assert project_scope_id(root).startswith("scope:")


def test_same_basename_under_different_parents_gets_distinct_scope() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        first = Path(tmp) / "a" / "shared"
        second = Path(tmp) / "b" / "shared"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        assert project_scope_id(first) != project_scope_id(second)
        assert project_scope_slug(first) != project_scope_slug(second)
        assert legacy_project_key(first) == legacy_project_key(second) == "shared"


def test_normalized_path_uses_forward_slashes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert "\\" not in normalize_project_path(tmp)
