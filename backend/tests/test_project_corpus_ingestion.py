from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import indexing
from app.application.projects.scope import project_scope_id
from app.application.rag_memory import runtime as rag_runtime
from app.application.rag_memory import service as rag_service


def _connection_factory(db_path: Path) -> Callable[[], sqlite3.Connection]:
    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    return connect


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _init_repo(repo: Path, *, commit: bool = False) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "--quiet")
    if commit:
        _git(repo, "config", "user.email", "elira-tests@example.invalid")
        _git(repo, "config", "user.name", "Elira Tests")


@pytest.fixture()
def corpus_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Callable[[], sqlite3.Connection]]:
    db_path = tmp_path / "rag.sqlite3"
    conn_factory = _connection_factory(db_path)
    rag_runtime.init_db(conn_factory=conn_factory)

    def add_to_rag(**kwargs: Any) -> dict[str, Any]:
        return rag_runtime.add_to_rag(
            conn_factory=conn_factory,
            get_embedding_func=lambda _text: None,
            **kwargs,
        )

    monkeypatch.setattr(rag_service, "_conn", conn_factory)
    monkeypatch.setattr(rag_service, "add_to_rag", add_to_rag)
    return db_path, conn_factory


def test_gitignore_metadata_incremental_update_and_stale_cleanup(
    tmp_path: Path,
    corpus_db: tuple[Path, Callable[[], sqlite3.Connection]],
) -> None:
    _db_path, conn_factory = corpus_db
    project = tmp_path / "project"
    _init_repo(project, commit=True)
    (project / ".gitignore").write_text("ignored.py\n", encoding="utf-8", newline="\n")
    (project / "keep.py").write_text("def indexed_marker():\n    return 1\n", encoding="utf-8", newline="\n")
    (project / "ignored.py").write_text("SECRET_IGNORED_MARKER = True\n", encoding="utf-8", newline="\n")
    _git(project, "add", ".gitignore", "keep.py")
    _git(project, "commit", "--quiet", "-m", "initial")

    first = indexing.index_project(project)
    assert first["ok"] is True
    assert first["complete"] is True
    assert first["files_scanned"] == 2
    assert first["files_indexed"] == 2
    assert first["repositories"] == 1

    conn = conn_factory()
    try:
        rows = conn.execute(
            "SELECT source_uri, source_hash, metadata_json, text FROM rag_items ORDER BY source_uri"
        ).fetchall()
    finally:
        conn.close()
    assert {row["source_uri"] for row in rows} == {".gitignore", "keep.py"}
    assert all("SECRET_IGNORED_MARKER" not in row["text"] for row in rows)
    keep = next(row for row in rows if row["source_uri"] == "keep.py")
    metadata = json.loads(keep["metadata_json"])
    assert metadata["repo"] == "."
    assert metadata["file"] == "keep.py"
    assert metadata["language"] == "python"
    assert len(metadata["commit"]) >= 7

    second = indexing.index_project(project)
    assert second["files_indexed"] == 0
    assert second["files_unchanged"] == 2
    assert second["chunks_indexed"] == 0

    prior_hash = keep["source_hash"]
    (project / "keep.py").write_text("def indexed_marker():\n    return 2\n", encoding="utf-8", newline="\n")
    changed = indexing.index_project(project)
    assert changed["files_indexed"] == 1
    assert changed["files_unchanged"] == 1
    assert changed["deleted_chunks"] >= 1

    conn = conn_factory()
    try:
        keep_rows = conn.execute(
            "SELECT source_hash, text FROM rag_items WHERE source_uri = 'keep.py'"
        ).fetchall()
    finally:
        conn.close()
    assert len(keep_rows) == 1
    assert keep_rows[0]["source_hash"] != prior_hash
    assert "return 2" in keep_rows[0]["text"]

    (project / "keep.py").unlink()
    removed = indexing.index_project(project)
    assert removed["stale_files_removed"] == 1
    conn = conn_factory()
    try:
        assert conn.execute("SELECT COUNT(*) FROM rag_items WHERE source_uri = 'keep.py'").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM project_corpus_files WHERE source_uri = 'keep.py'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_failed_file_is_resumable_and_source_dedup_is_idempotent(
    tmp_path: Path,
    corpus_db: tuple[Path, Callable[[], sqlite3.Connection]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, conn_factory = corpus_db
    project = tmp_path / "plain-project"
    project.mkdir()
    source = project / "main.py"
    source.write_text("print('resume-marker')\n", encoding="utf-8", newline="\n")

    monkeypatch.setattr(rag_service, "add_to_rag", lambda **_kwargs: {"ok": False, "error": "offline"})
    failed = indexing.index_project(project)
    assert failed["complete"] is False
    assert failed["resume_required"] is True
    assert failed["files_failed"] == 1

    conn = conn_factory()
    try:
        status = conn.execute(
            "SELECT status FROM project_corpus_files WHERE project = ? AND source_uri = 'main.py'",
            (project_scope_id(project),),
        ).fetchone()["status"]
    finally:
        conn.close()
    assert status == "failed"

    def add_to_rag(**kwargs: Any) -> dict[str, Any]:
        return rag_runtime.add_to_rag(
            conn_factory=conn_factory,
            get_embedding_func=lambda _text: None,
            **kwargs,
        )

    monkeypatch.setattr(rag_service, "add_to_rag", add_to_rag)
    resumed = indexing.index_project(project)
    assert resumed["complete"] is True
    assert resumed["files_indexed"] == 1
    assert resumed["chunks_created"] == 1

    third = indexing.index_project(project)
    assert third["files_unchanged"] == 1
    conn = conn_factory()
    try:
        rows = conn.execute("SELECT importance FROM rag_items WHERE source_uri = 'main.py'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["importance"] == 4


def test_container_root_unifies_multiple_repositories_and_reports_status(
    tmp_path: Path,
    corpus_db: tuple[Path, Callable[[], sqlite3.Connection]],
) -> None:
    _db_path, conn_factory = corpus_db
    corpus_root = tmp_path / "corpus"
    first_repo = corpus_root / "alpha"
    second_repo = corpus_root / "beta"
    _init_repo(first_repo)
    _init_repo(second_repo)
    (first_repo / "alpha.py").write_text("ALPHA_NETWORK_TOKEN = 1\n", encoding="utf-8", newline="\n")
    (second_repo / "beta.ts").write_text("export const BETA_NETWORK_TOKEN = 2;\n", encoding="utf-8", newline="\n")

    result = indexing.index_project(corpus_root)
    assert result["complete"] is True
    assert result["repositories"] == 2
    assert result["files_scanned"] == 2

    status = indexing.project_corpus_status(corpus_root)
    assert status["ok"] is True
    assert status["files"] == 2
    assert status["chunks"] == 2
    assert status["repositories"] == 2
    assert status["by_status"] == {"indexed": 2}
    assert status["by_language"] == {"python": 1, "typescript": 1}

    conn = conn_factory()
    try:
        metadata = [
            json.loads(row["metadata_json"])
            for row in conn.execute("SELECT metadata_json FROM rag_items ORDER BY source_uri").fetchall()
        ]
    finally:
        conn.close()
    assert {item["repo"] for item in metadata} == {"alpha", "beta"}


def test_search_exposes_corpus_metadata_without_internal_hash(
    corpus_db: tuple[Path, Callable[[], sqlite3.Connection]],
) -> None:
    _db_path, conn_factory = corpus_db
    rag_runtime.add_to_rag(
        conn_factory=conn_factory,
        get_embedding_func=lambda _text: None,
        text="[file:src/net.py:1-2]\nNETWORK_DIAGNOSTIC_MARKER = True",
        category="code_index",
        project="scope:corpus",
        source_uri="src/net.py",
        source_hash="sha256-value",
        metadata={"repo": "network-tools", "commit": "abc123", "language": "python"},
    )
    result = rag_runtime.search_rag(
        conn_factory=conn_factory,
        get_embedding_func=lambda _query: None,
        cosine_sim_func=rag_runtime.cosine_sim,
        query="NETWORK_DIAGNOSTIC_MARKER",
        project="scope:corpus",
        min_score=0.0,
    )
    item = result["items"][0]
    assert item["source"] == {
        "file": "src/net.py",
        "start": 1,
        "end": 2,
        "repo": "network-tools",
        "commit": "abc123",
        "language": "python",
    }
    assert item["metadata"]["repo"] == "network-tools"
    assert "source_hash" not in item
    assert "metadata_json" not in item


def test_search_supplements_candidate_cap_with_full_corpus_lexical_matches(
    corpus_db: tuple[Path, Callable[[], sqlite3.Connection]],
) -> None:
    _db_path, conn_factory = corpus_db
    for index in range(4):
        rag_runtime.add_to_rag(
            conn_factory=conn_factory,
            get_embedding_func=lambda _text: None,
            text=f"high importance unrelated entry {index}",
            importance=10,
            project="scope:large",
        )
    rag_runtime.add_to_rag(
        conn_factory=conn_factory,
        get_embedding_func=lambda _text: None,
        text="late EXACT_CORPUS_IDENTIFIER implementation",
        importance=1,
        project="scope:large",
    )

    result = rag_runtime.search_rag(
        conn_factory=conn_factory,
        get_embedding_func=lambda _query: None,
        cosine_sim_func=rag_runtime.cosine_sim,
        query="EXACT_CORPUS_IDENTIFIER",
        project="scope:large",
        candidate_limit=2,
        min_score=0.1,
    )
    assert any("EXACT_CORPUS_IDENTIFIER" in item["text"] for item in result["items"])


def test_incomplete_git_snapshot_preserves_existing_corpus(
    tmp_path: Path,
    corpus_db: tuple[Path, Callable[[], sqlite3.Connection]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, conn_factory = corpus_db
    project = tmp_path / "git-project"
    _init_repo(project)
    (project / "keep.py").write_text("KEEP_ON_GIT_FAILURE = True\n", encoding="utf-8", newline="\n")
    assert indexing.index_project(project)["complete"] is True

    monkeypatch.setattr(
        indexing,
        "_git_visible_files",
        lambda _repo: (None, "simulated git failure"),
    )
    result = indexing.index_project(project)
    assert result["ok"] is False
    assert "preserved" in result["error"]

    conn = conn_factory()
    try:
        assert conn.execute("SELECT COUNT(*) FROM rag_items WHERE source_uri = 'keep.py'").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM project_corpus_files WHERE source_uri = 'keep.py'"
        ).fetchone()[0] == 1
    finally:
        conn.close()
