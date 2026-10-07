"""One root sandbox with a folder per chat; skills stay shared (John 2026-10-07)."""
from __future__ import annotations

from pathlib import Path

from app.api.routes.code_agent_routes import _resolve_project_root
from app.application.code_agent.prompts import _NO_PROJECT_BLOCK, _is_scratch_workspace
from app.core.data_files import data_subdir


def test_chat_without_project_gets_its_own_folder() -> None:
    root = data_subdir("agent_workspace")

    first = Path(_resolve_project_root("", "s-1-abc123"))
    second = Path(_resolve_project_root(None, "s-2-def456"))

    assert first == root / "chats" / "s-1-abc123"
    assert second == root / "chats" / "s-2-def456"
    assert first.is_dir() and second.is_dir()


def test_explicit_project_and_unsafe_ids_keep_their_root(tmp_path: Path) -> None:
    root = data_subdir("agent_workspace")

    assert _resolve_project_root(str(tmp_path), "s-1-abc123") == str(tmp_path)
    assert _resolve_project_root("", "../escape") == str(root)
    assert _resolve_project_root("", None) == str(root)
    assert not (root / "escape").exists()


def test_chat_folders_count_as_no_project(tmp_path: Path) -> None:
    root = data_subdir("agent_workspace")
    chat = Path(_resolve_project_root("", "s-3-aaa111"))

    assert _is_scratch_workspace(root)
    assert _is_scratch_workspace(chat)
    assert not _is_scratch_workspace(chat / "nested")
    assert not _is_scratch_workspace(tmp_path)


def test_no_project_block_names_chat_folder_and_skills() -> None:
    assert "data/agent_workspace/chats" in _NO_PROJECT_BLOCK
    assert "навык" in _NO_PROJECT_BLOCK
