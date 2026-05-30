"""Tests for the saved-projects registry (advanced/projects_registry)."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _fresh_registry(tmp_data: str):
    os.environ["ELIRA_DATA_DIR"] = tmp_data
    from app.core import data_files
    importlib.reload(data_files)
    from app.application.advanced import projects_registry
    importlib.reload(projects_registry)
    return projects_registry


def test_add_list_remove_roundtrip():
    with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as proj:
        reg = _fresh_registry(data)
        try:
            assert reg.list_projects()["projects"] == []

            added = reg.add_project(proj, name="Repro")
            assert added["ok"] and added["name"] == "Repro"

            items = reg.list_projects()["projects"]
            assert len(items) == 1 and items[0]["path"] == str(Path(proj).resolve())

            pid = items[0]["id"]
            assert reg.remove_project(pid)["removed"] is True
            assert reg.list_projects()["projects"] == []
        finally:
            os.environ.pop("ELIRA_DATA_DIR", None)


def test_add_rejects_missing_dir():
    with tempfile.TemporaryDirectory() as data:
        reg = _fresh_registry(data)
        try:
            res = reg.add_project("/no/such/dir/xyz123")
            assert res["ok"] is False
        finally:
            os.environ.pop("ELIRA_DATA_DIR", None)


def test_add_is_idempotent_on_same_path_updates_name():
    with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as proj:
        reg = _fresh_registry(data)
        try:
            reg.add_project(proj, name="First")
            reg.add_project(proj, name="Second")
            items = reg.list_projects()["projects"]
            assert len(items) == 1            # same path -> one row
            assert items[0]["name"] == "Second"
        finally:
            os.environ.pop("ELIRA_DATA_DIR", None)


def test_resolve_by_name_and_path():
    with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as proj:
        reg = _fresh_registry(data)
        try:
            reg.add_project(proj, name="Repro Server")
            by_name = reg.resolve_project("repro server")  # case-insensitive
            assert by_name is not None and by_name["path"] == str(Path(proj).resolve())
            by_path = reg.resolve_project(str(Path(proj).resolve()))
            assert by_path is not None
            assert reg.resolve_project("nonexistent") is None
        finally:
            os.environ.pop("ELIRA_DATA_DIR", None)
