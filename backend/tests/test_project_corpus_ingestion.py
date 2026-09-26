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

    monkeypatch.setattr(rag_service, "_conn", conn_factory)
    monkeypatch.setattr(rag_service, "_get_embedding", lambda _text: None)
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

    def offline(_text: str) -> None:
        raise RuntimeError("offline")

    monkeypatch.setattr(rag_service, "_get_embedding", offline)
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

    monkeypatch.setattr(rag_service, "_get_embedding", lambda _text: None)
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


def _write_version(source: Path, version: str) -> None:
    # The real chunker makes two chunks, both with a searchable version marker.
    source.write_text("".join(f"CORPUS_MARKER {version} line {i}\n" for i in range(120)), encoding="utf-8", newline="\n")


def _source_state(conn_factory: Callable[[], sqlite3.Connection], project: Path) -> dict[str, Any]:
    conn = conn_factory()
    try:
        scope = project_scope_id(project)
        conn.execute("BEGIN")
        return {
            "chunks": [dict(row) for row in conn.execute(
                "SELECT id, source_uri, source_hash, text, importance FROM rag_items WHERE project = ? ORDER BY id",
                (scope,),
            )],
            "manifest": [dict(row) for row in conn.execute(
                "SELECT * FROM project_corpus_files WHERE project = ? ORDER BY source_uri", (scope,),
            )],
        }
    finally:
        conn.close()


def _recall(conn_factory: Callable[[], sqlite3.Connection], project: Path) -> list[dict[str, Any]]:
    return rag_runtime.search_rag(
        conn_factory=conn_factory, get_embedding_func=lambda _text: None,
        cosine_sim_func=rag_runtime.cosine_sim, query="CORPUS_MARKER",
        project=project_scope_id(project), limit=20,
    )["items"]


@pytest.mark.parametrize("has_previous", [False, True])
def test_partial_preparation_preserves_published_source_then_retries_in_scope(
    tmp_path: Path,
    corpus_db: tuple[Path, Callable[[], sqlite3.Connection]],
    monkeypatch: pytest.MonkeyPatch,
    has_previous: bool,
) -> None:
    _db_path, conn_factory = corpus_db
    project, other = tmp_path / "project", tmp_path / "other"
    project.mkdir()
    other.mkdir()
    source = project / "inventory.txt"
    _write_version(other / source.name, "other")
    assert indexing.index_project(other)["complete"] is True
    other_before = _source_state(conn_factory, other)
    if has_previous:
        _write_version(source, "v1")
        assert indexing.index_project(project)["complete"] is True
    before = _source_state(conn_factory, project)
    _write_version(source, "v2")
    calls = 0

    def interrupted_embedding(_text: str) -> list[float]:
        nonlocal calls
        calls += 1
        visible = _recall(conn_factory, project)
        assert len(visible) == (2 if has_previous else 0)
        assert all("v1" in row["text"] for row in visible)
        if calls == 2:
            raise RuntimeError("second chunk embedding interrupted")
        return [1.0, 0.0]

    monkeypatch.setattr(rag_service, "_get_embedding", interrupted_embedding)
    failed = indexing.index_project(project)
    assert calls == 2
    assert failed["complete"] is False and failed["resume_required"] is True
    assert failed["chunks_created"] == failed["chunks_indexed"] == 0
    after_failure = _source_state(conn_factory, project)
    assert after_failure["chunks"] == before["chunks"]
    if has_previous:
        assert after_failure == before
    else:
        assert after_failure["manifest"][0]["status"] == "failed"
        assert after_failure["manifest"][0]["chunk_count"] == 0
    assert len(_recall(conn_factory, project)) == (2 if has_previous else 0)

    monkeypatch.setattr(rag_service, "_get_embedding", lambda _text: None)
    assert indexing.index_project(project)["complete"] is True
    published = _source_state(conn_factory, project)
    assert len(published["chunks"]) == 2
    assert {row["source_hash"] for row in published["chunks"]} == {published["manifest"][0]["content_hash"]}
    assert all("v2" in row["text"] for row in _recall(conn_factory, project))
    assert indexing.index_project(project)["files_unchanged"] == 1
    assert _source_state(conn_factory, project) == published
    source.unlink()
    assert indexing.index_project(project)["stale_files_removed"] == 1
    assert _recall(conn_factory, project) == []
    assert _source_state(conn_factory, project) == {"chunks": [], "manifest": []}
    assert _source_state(conn_factory, other) == other_before


@pytest.mark.parametrize("failure_at", ["second_chunk", "manifest"])
def test_sql_failure_rolls_back_source_and_manifest_as_one_publication(
    tmp_path: Path,
    corpus_db: tuple[Path, Callable[[], sqlite3.Connection]],
    monkeypatch: pytest.MonkeyPatch,
    failure_at: str,
) -> None:
    _db_path, conn_factory = corpus_db
    project = tmp_path / "project"
    project.mkdir()
    source = project / "inventory.txt"
    _write_version(source, "v1")
    assert indexing.index_project(project)["complete"] is True
    before = _source_state(conn_factory, project)
    _write_version(source, "v2")
    observed: list[dict[str, Any]] = []

    def observe() -> int:
        # A separate SQLite reader during DELETE/INSERT still sees complete v1.
        observed.append(_source_state(conn_factory, project))
        return 0

    def writer_connection() -> sqlite3.Connection:
        conn = conn_factory()
        conn.create_function("observe_publication", 0, observe)
        return conn

    conn = writer_connection()
    try:
        target = (
            "BEFORE INSERT ON rag_items WHEN NEW.source_uri = 'inventory.txt' AND NEW.text LIKE '%:71-%'"
            if failure_at == "second_chunk" else
            "BEFORE UPDATE ON project_corpus_files WHEN NEW.status = 'indexed'"
        )
        conn.execute(f"""
            CREATE TRIGGER fail_publication {target}
            BEGIN
                SELECT observe_publication();
                SELECT RAISE(ABORT, 'controlled SQL publication failure');
            END
        """)
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(rag_service, "_conn", writer_connection)
    failed = indexing.index_project(project)
    assert failed["complete"] is False
    assert "controlled SQL publication failure" in failed["errors"][0]
    assert observed == [before]
    assert _source_state(conn_factory, project) == before
    assert all("v1" in row["text"] for row in _recall(conn_factory, project))
    conn = conn_factory()
    try:
        conn.execute("DROP TRIGGER fail_publication")
        conn.commit()
    finally:
        conn.close()
    assert indexing.index_project(project)["complete"] is True
    assert all("v2" in row["text"] for row in _recall(conn_factory, project))


def test_search_snapshot_does_not_mix_versions_committed_between_shortlists(
    tmp_path: Path,
    corpus_db: tuple[Path, Callable[[], sqlite3.Connection]],
) -> None:
    db_path, conn_factory = corpus_db
    conn = conn_factory()
    try:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    finally:
        conn.close()
    project = tmp_path / "project"
    project.mkdir()
    source = project / "inventory.txt"
    _write_version(source, "v1")
    assert indexing.index_project(project)["complete"] is True
    _write_version(source, "v2")
    published = False

    class InterleavedReader(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            nonlocal published
            if "LOWER(text) LIKE" in sql and not published:
                # A real second connection commits after the vector shortlist
                # was fetched, before the lexical shortlist is selected.
                published = True
                assert indexing.index_project(project)["complete"] is True
            return super().execute(sql, parameters)

    def reader_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(db_path, factory=InterleavedReader)
        conn.row_factory = sqlite3.Row
        return conn

    visible = _recall(reader_connection, project)
    assert published
    assert len(visible) == 2 and all("v1" in row["text"] for row in visible)
    after = _recall(conn_factory, project)
    assert len(after) == 2 and all("v2" in row["text"] for row in after)


def test_changed_source_cannot_overwrite_newer_concurrent_publication(
    tmp_path: Path,
    corpus_db: tuple[Path, Callable[[], sqlite3.Connection]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, conn_factory = corpus_db
    project = tmp_path / "project"
    project.mkdir()
    source = project / "inventory.txt"
    _write_version(source, "v1")
    assert indexing.index_project(project)["complete"] is True
    _write_version(source, "v2")
    newer: dict[str, Any] = {}

    def prepare_old_version(_text: str) -> None:
        if not newer:
            # Model a second publisher finishing while the first prepares v2.
            _write_version(source, "v3")
            with monkeypatch.context() as patch:
                patch.setattr(rag_service, "_get_embedding", lambda _text: None)
                assert indexing.index_project(project)["complete"] is True
            newer.update(_source_state(conn_factory, project))

    monkeypatch.setattr(rag_service, "_get_embedding", prepare_old_version)
    failed = indexing.index_project(project)
    assert failed["complete"] is False
    assert "Source changed during preparation" in failed["errors"][0]
    assert _source_state(conn_factory, project) == newer
    assert all("v3" in row["text"] for row in _recall(conn_factory, project))
    assert indexing.index_project(project)["files_unchanged"] == 1


def test_legacy_partial_rows_are_hidden_until_successful_source_reindex(
    tmp_path: Path,
    corpus_db: tuple[Path, Callable[[], sqlite3.Connection]],
) -> None:
    _db_path, conn_factory = corpus_db
    project = tmp_path / "project"
    project.mkdir()
    source = project / "inventory.txt"
    _write_version(source, "v1")
    assert indexing.index_project(project)["complete"] is True
    old_hash = _source_state(conn_factory, project)["manifest"][0]["content_hash"]
    _write_version(source, "v2")
    # Recreate persisted old-code damage: v1 plus just one committed v2 chunk.
    rag_runtime.add_to_rag(
        conn_factory=conn_factory, get_embedding_func=lambda _text: None,
        text=next(indexing._chunk_file(source, project))[0], category="code_index",
        project=project_scope_id(project), source_uri=source.name,
        source_hash=indexing._content_hash(source),
    )
    # A source-backed legacy row without a manifest retains its old semantics.
    rag_runtime.add_to_rag(
        conn_factory=conn_factory, get_embedding_func=lambda _text: None,
        text="CORPUS_MARKER legacy unmanaged", category="code_index",
        project=project_scope_id(project), source_uri="legacy.txt", source_hash="legacy",
    )
    conn = conn_factory()
    try:
        for status in ("failed", "pending"):
            conn.execute("UPDATE project_corpus_files SET status = ?", (status,))
            conn.commit()
            visible = _recall(conn_factory, project)
            assert len(visible) == 1 and "legacy unmanaged" in visible[0]["text"]
        # A valid manifest exposes only its declared version, not orphan rows.
        conn.execute("UPDATE project_corpus_files SET status = 'indexed', content_hash = ?", (old_hash,))
        conn.commit()
        visible = _recall(conn_factory, project)
        assert len(visible) == 3 and not any("v2" in row["text"] for row in visible)
        conn.execute("UPDATE project_corpus_files SET status = 'failed'")
        conn.commit()
    finally:
        conn.close()
    assert len(_source_state(conn_factory, project)["chunks"]) == 4  # reads did not delete anything
    assert indexing.index_project(project)["complete"] is True
    visible = _recall(conn_factory, project)
    assert len(visible) == 3
    assert sum("v2" in row["text"] for row in visible) == 2
    assert not any("v1" in row["text"] for row in visible)
    # Standalone older RAG databases may not have a corpus manifest at all.
    conn = conn_factory()
    try:
        conn.execute("DROP TABLE project_corpus_files")
        conn.commit()
    finally:
        conn.close()
    assert len(_recall(conn_factory, project)) == 3
