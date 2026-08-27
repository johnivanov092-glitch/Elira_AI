"""Incremental project-corpus ingestion backed by the existing RAG store.

The selected project root is the corpus boundary. It may be one repository, a
monorepo, or a directory containing many Git repositories. File-level hashes in
``project_corpus_files`` make repeated runs incremental and resumable; searchable
chunks stay in ``rag_items``.

Public names are re-exported from ``agent_loop`` for backward compatibility.
"""
from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from app.application.projects.scope import legacy_project_key, project_scope_id

logger = logging.getLogger(__name__)

PROJECT_PROMPT_FILENAME = ".elira/agent.md"

DEFAULT_INDEX_PATTERNS = [
    "**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs",
    "**/*.md", "**/*.rst", "**/*.txt",
    "**/*.rs", "**/*.go", "**/*.java", "**/*.kt", "**/*.kts",
    "**/*.cpp", "**/*.cc", "**/*.c", "**/*.h", "**/*.hpp", "**/*.cs",
    "**/*.php", "**/*.rb", "**/*.swift", "**/*.scala",
    "**/*.sh", "**/*.bash", "**/*.ps1", "**/*.bat", "**/*.cmd",
    "**/*.json", "**/*.jsonc", "**/*.toml", "**/*.yaml", "**/*.yml", "**/*.xml",
    "**/*.sql", "**/*.proto", "**/*.graphql", "**/*.gql", "**/*.ini", "**/*.cfg",
    "**/Dockerfile", "Dockerfile", "**/Makefile", "Makefile",
    "**/CMakeLists.txt", "CMakeLists.txt", "**/.gitignore", ".gitignore",
]
INDEX_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    "target", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", "coverage",
    "data",
}
INDEX_CHUNK_LINES = 80
INDEX_CHUNK_OVERLAP = 10
INDEX_MAX_FILE_BYTES = 200_000
INDEX_MAX_TOTAL_CHUNKS = 5000
_INDEX_LOCK = threading.RLock()

_LANGUAGE_BY_SUFFIX = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript-react",
    ".js": "javascript", ".jsx": "javascript-react", ".mjs": "javascript",
    ".cjs": "javascript", ".md": "markdown", ".rst": "restructuredtext",
    ".txt": "text", ".rs": "rust", ".go": "go", ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin", ".cpp": "cpp", ".cc": "cpp",
    ".c": "c", ".h": "c", ".hpp": "cpp", ".cs": "csharp", ".php": "php",
    ".rb": "ruby", ".swift": "swift", ".scala": "scala", ".sh": "shell",
    ".bash": "shell", ".ps1": "powershell", ".bat": "batch", ".cmd": "batch",
    ".json": "json", ".jsonc": "jsonc", ".toml": "toml", ".yaml": "yaml",
    ".yml": "yaml", ".xml": "xml", ".sql": "sql", ".proto": "protobuf",
    ".graphql": "graphql", ".gql": "graphql", ".ini": "ini", ".cfg": "config",
}
_LANGUAGE_BY_NAME = {
    "Dockerfile": "dockerfile",
    "Makefile": "makefile",
    "CMakeLists.txt": "cmake",
    ".gitignore": "gitignore",
}


@dataclass(frozen=True)
class CorpusFile:
    path: Path
    source_uri: str
    repo: str
    repo_name: str
    commit_sha: str
    language: str
    size_bytes: int
    mtime_ns: int


def _posix_relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _matches_patterns(rel_path: str, patterns: list[str]) -> bool:
    candidate = PurePosixPath(rel_path)
    return any(
        candidate.match(pattern)
        or fnmatch.fnmatch(rel_path, pattern)
        or (pattern.startswith("**/") and fnmatch.fnmatch(rel_path, pattern[3:]))
        for pattern in patterns
    )


def _path_in_skip_dir(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    skipped = {name.casefold() for name in INDEX_SKIP_DIRS}
    return any(part.casefold() in skipped for part in parts[:-1])


def _chunk_file(file_path: Path, project_root: Path) -> Iterator[tuple[str, int, int]]:
    """Yield ``(text, start_line, end_line)`` chunks for one text file."""
    try:
        size = file_path.stat().st_size
    except OSError:
        return
    if size > INDEX_MAX_FILE_BYTES:
        return
    try:
        with file_path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            lines = fh.readlines()
    except Exception:
        return
    if not lines:
        return
    rel = _posix_relative(file_path, project_root)
    step = max(1, INDEX_CHUNK_LINES - INDEX_CHUNK_OVERLAP)
    for start in range(0, len(lines), step):
        end = min(start + INDEX_CHUNK_LINES, len(lines))
        if end <= start:
            break
        chunk_text = "".join(lines[start:end])
        if not chunk_text.strip():
            continue
        yield f"[file:{rel}:{start + 1}-{end}]\n{chunk_text}", start + 1, end
        if end >= len(lines):
            break


def _discover_git_repositories(root: Path) -> list[Path]:
    repositories: list[Path] = []
    skipped = {name.casefold() for name in INDEX_SKIP_DIRS}
    for current, dir_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        if ".git" in dir_names or ".git" in file_names:
            repositories.append(current_path.resolve())
        dir_names[:] = [
            name for name in dir_names
            if name != ".git" and name.casefold() not in skipped
        ]
    return sorted(set(repositories), key=lambda item: (len(item.parts), str(item).casefold()))


def _run_git(repo: Path, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _git_visible_files(repo: Path) -> tuple[set[Path] | None, str | None]:
    result = _run_git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if result is None:
        return None, f"Git unavailable while reading ignore rules for {repo}"
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        return None, f"git ls-files failed for {repo}: {message or result.returncode}"
    visible: set[Path] = set()
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="surrogateescape")
        candidate = (repo / rel).resolve()
        if candidate.is_file():
            visible.add(candidate)
    return visible, None


def _git_commit(repo: Path) -> str:
    result = _run_git(repo, "rev-parse", "HEAD", timeout=10.0)
    if result is None or result.returncode != 0:
        return ""
    return result.stdout.decode("ascii", errors="ignore").strip()


def _repo_label(repo: Path, root: Path) -> str:
    try:
        rel = _posix_relative(repo, root)
    except ValueError:
        return repo.name
    return rel or "."


def _language_for(path: Path) -> str:
    return _LANGUAGE_BY_NAME.get(path.name, _LANGUAGE_BY_SUFFIX.get(path.suffix.casefold(), "text"))


def _record_for(path: Path, root: Path, repo: Path | None, commit_sha: str = "") -> CorpusFile | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size > INDEX_MAX_FILE_BYTES:
        return None
    repo_path = repo or root
    return CorpusFile(
        path=path,
        source_uri=_posix_relative(path, root),
        repo=_repo_label(repo_path, root),
        repo_name=repo_path.name,
        commit_sha=commit_sha,
        language=_language_for(path),
        size_bytes=int(stat.st_size),
        mtime_ns=int(stat.st_mtime_ns),
    )


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _build_corpus_files(root: Path, patterns: list[str]) -> tuple[list[CorpusFile], list[str]]:
    """Build a deterministic, de-duplicated corpus snapshot.

    Git repositories are enumerated through ``git ls-files`` so tracked files
    and non-ignored untracked files are included with native nested
    ``.gitignore`` semantics. Files outside discovered repositories use the
    explicit extension/name allow-list plus the standard skip directories.
    """
    repositories = _discover_git_repositories(root)
    records: dict[Path, CorpusFile] = {}
    errors: list[str] = []

    # Shallow repositories run first; a nested repository then overwrites the
    # same physical path with the more precise repo/commit metadata.
    for repo in repositories:
        visible, error = _git_visible_files(repo)
        if error:
            errors.append(error)
        if visible is None:
            continue
        commit_sha = _git_commit(repo)
        for path in sorted(visible, key=lambda item: str(item).casefold()):
            if not _is_under(path, root) or _path_in_skip_dir(path, root):
                continue
            rel = _posix_relative(path, root)
            if not _matches_patterns(rel, patterns):
                continue
            record = _record_for(path, root, repo, commit_sha)
            if record is not None:
                records[path] = record

    # A corpus root may simply be a directory of repositories with additional
    # shared files. Include only paths not owned by a discovered Git repo here.
    skipped = {name.casefold() for name in INDEX_SKIP_DIRS}
    for current, dir_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        dir_names[:] = [name for name in dir_names if name.casefold() not in skipped]
        for name in file_names:
            path = (current_path / name).resolve()
            if any(_is_under(path, repo) for repo in repositories):
                continue
            if _path_in_skip_dir(path, root):
                continue
            rel = _posix_relative(path, root)
            if not _matches_patterns(rel, patterns):
                continue
            record = _record_for(path, root, None)
            if record is not None:
                records[path] = record

    return sorted(records.values(), key=lambda item: item.source_uri.casefold()), errors


def _iter_project_files(project_root: Path, patterns: list[str]) -> Iterator[Path]:
    """Compatibility iterator used by older imports/tests."""
    records, _errors = _build_corpus_files(project_root.resolve(), patterns)
    for record in records:
        yield record.path


def _content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_manifest(conn: Any, scope_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT source_uri, content_hash, chunk_count, status, error
        FROM project_corpus_files
        WHERE project = ?
        """,
        (scope_id,),
    ).fetchall()
    return {str(row["source_uri"]): dict(row) for row in rows}


def _upsert_manifest(
    conn: Any,
    *,
    scope_id: str,
    record: CorpusFile,
    content_hash: str,
    chunk_count: int,
    status: str,
    error: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO project_corpus_files (
            project, source_uri, content_hash, size_bytes, mtime_ns, chunk_count,
            status, error, repo, commit_sha, language, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(project, source_uri) DO UPDATE SET
            content_hash = excluded.content_hash,
            size_bytes = excluded.size_bytes,
            mtime_ns = excluded.mtime_ns,
            chunk_count = excluded.chunk_count,
            status = excluded.status,
            error = excluded.error,
            repo = excluded.repo,
            commit_sha = excluded.commit_sha,
            language = excluded.language,
            indexed_at = CURRENT_TIMESTAMP
        """,
        (
            scope_id, record.source_uri, content_hash, record.size_bytes, record.mtime_ns,
            int(chunk_count), status, error, record.repo, record.commit_sha, record.language,
        ),
    )
    conn.commit()


def _delete_source_chunks(conn: Any, scope_id: str, source_uri: str, *, keep_hash: str | None = None) -> int:
    sql = "DELETE FROM rag_items WHERE category = ? AND project = ? AND source_uri = ?"
    params: list[Any] = ["code_index", scope_id, source_uri]
    if keep_hash is not None:
        sql += " AND COALESCE(source_hash, '') != ?"
        params.append(keep_hash)
    cursor = conn.execute(sql, params)
    conn.commit()
    return int(cursor.rowcount or 0)


def _cleanup_stale_files(conn: Any, scope_id: str, stale_uris: set[str]) -> tuple[int, int]:
    deleted_chunks = 0
    for source_uri in sorted(stale_uris):
        deleted_chunks += _delete_source_chunks(conn, scope_id, source_uri)
        conn.execute(
            "DELETE FROM project_corpus_files WHERE project = ? AND source_uri = ?",
            (scope_id, source_uri),
        )
    conn.commit()
    return len(stale_uris), deleted_chunks


def _index_project_unlocked(
    project_root: Path | str,
    *,
    patterns: list[str] | None = None,
    replace: bool = True,
) -> dict[str, Any]:
    """Incrementally ingest one project/corpus root into the existing RAG DB.

    ``replace=True`` reconciles the durable manifest with the current filesystem
    snapshot. Unchanged files retain their embeddings, changed files are
    versioned by content hash, deleted files are removed, and an interrupted run
    resumes on the next call without rebuilding completed files.
    """
    root = Path(project_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": f"project_root does not exist: {root}"}

    try:
        from app.application.rag_memory.service import _conn, add_to_rag
    except Exception as exc:
        return {"ok": False, "error": f"RAG service unavailable: {exc}"}

    pats = list(patterns or DEFAULT_INDEX_PATTERNS)
    records, discovery_errors = _build_corpus_files(root, pats)
    # Reconciliation must never run from an incomplete snapshot: a temporary
    # Git failure would otherwise make every file in that repository look
    # deleted. Fail closed and leave the previous corpus untouched.
    if discovery_errors:
        return {
            "ok": False,
            "error": "Corpus snapshot is incomplete; existing index was preserved",
            "project_root": str(root),
            "errors": discovery_errors,
        }
    scope_id = project_scope_id(root)
    legacy_key = legacy_project_key(root)
    conn = _conn()
    try:
        manifest = _load_manifest(conn, scope_id)
    finally:
        conn.close()

    files_indexed = 0
    files_unchanged = 0
    files_failed = 0
    chunks_indexed = 0
    chunks_created = 0
    chunks_reused = 0
    failed_chunks = 0
    deleted_chunks = 0
    errors = list(discovery_errors)
    resume_required = False
    observed_uris = {record.source_uri for record in records}

    for record in records:
        prior = manifest.get(record.source_uri) or {}
        try:
            content_hash = _content_hash(record.path)
        except OSError as exc:
            files_failed += 1
            errors.append(f"{record.source_uri}: {exc}")
            resume_required = True
            continue

        if prior.get("status") == "indexed" and prior.get("content_hash") == content_hash:
            files_unchanged += 1
            continue

        chunks = list(_chunk_file(record.path, root))
        remaining = INDEX_MAX_TOTAL_CHUNKS - chunks_indexed
        if len(chunks) > remaining:
            resume_required = True
            errors.append(f"Paused at INDEX_MAX_TOTAL_CHUNKS={INDEX_MAX_TOTAL_CHUNKS}; run indexing again to resume")
            break

        metadata = {
            "repo": record.repo,
            "repo_name": record.repo_name,
            "file": record.source_uri,
            "commit": record.commit_sha,
            "language": record.language,
        }
        indexed_for_file = 0
        file_errors: list[str] = []
        for chunk_text, _start, _end in chunks:
            try:
                result = add_to_rag(
                    text=chunk_text,
                    category="code_index",
                    importance=4,
                    project=scope_id,
                    source_uri=record.source_uri,
                    source_hash=content_hash,
                    metadata=metadata,
                )
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            if result.get("ok"):
                indexed_for_file += 1
                chunks_indexed += 1
                if result.get("action") == "deduped":
                    chunks_reused += 1
                else:
                    chunks_created += 1
            else:
                failed_chunks += 1
                file_errors.append(str(result.get("error") or "chunk ingestion failed"))

        conn = _conn()
        try:
            if file_errors:
                files_failed += 1
                message = "; ".join(file_errors[:3])
                _upsert_manifest(
                    conn, scope_id=scope_id, record=record, content_hash=content_hash,
                    chunk_count=indexed_for_file, status="failed", error=message,
                )
                errors.append(f"{record.source_uri}: {message}")
                resume_required = True
            else:
                deleted_chunks += _delete_source_chunks(
                    conn, scope_id, record.source_uri, keep_hash=content_hash,
                )
                _upsert_manifest(
                    conn, scope_id=scope_id, record=record, content_hash=content_hash,
                    chunk_count=indexed_for_file, status="indexed",
                )
                files_indexed += 1
        finally:
            conn.close()

    stale_files_removed = 0
    if replace:
        conn = _conn()
        try:
            stale = set(manifest) - observed_uris
            stale_files_removed, stale_chunks = _cleanup_stale_files(conn, scope_id, stale)
            deleted_chunks += stale_chunks
            # One-time migration cleanup for chunks created by the legacy
            # header-only indexer before source_uri/source_hash existed.
            if not resume_required and files_failed == 0:
                cursor = conn.execute(
                    """
                    DELETE FROM rag_items
                    WHERE category = ?
                      AND (project = ? OR project = ? OR COALESCE(project, '') = '')
                      AND COALESCE(source_uri, '') = ''
                    """,
                    ("code_index", scope_id, legacy_key),
                )
                deleted_chunks += int(cursor.rowcount or 0)
            conn.commit()
        finally:
            conn.close()

    return {
        "ok": True,
        "project_root": str(root),
        "project_scope": scope_id,
        "files_processed": len(records),
        "files_scanned": len(records),
        "files_indexed": files_indexed,
        "files_unchanged": files_unchanged,
        "files_failed": files_failed,
        "chunks_indexed": chunks_indexed,
        "chunks_created": chunks_created,
        "chunks_reused": chunks_reused,
        "failed_chunks": failed_chunks,
        "deleted_chunks": deleted_chunks,
        "stale_files_removed": stale_files_removed,
        "repositories": len({record.repo for record in records}),
        "resume_required": resume_required,
        "complete": not resume_required and files_failed == 0,
        "patterns": pats,
        "errors": errors,
    }


def index_project(
    project_root: Path | str,
    *,
    patterns: list[str] | None = None,
    replace: bool = True,
) -> dict[str, Any]:
    """Serialize ingestion so watcher/manual runs cannot duplicate source rows."""
    with _INDEX_LOCK:
        return _index_project_unlocked(project_root, patterns=patterns, replace=replace)


def project_corpus_status(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": f"project_root does not exist: {root}"}
    try:
        from app.application.rag_memory.service import _conn
    except Exception as exc:
        return {"ok": False, "error": f"RAG service unavailable: {exc}"}
    scope_id = project_scope_id(root)
    conn = _conn()
    try:
        summary = conn.execute(
            """
            SELECT COUNT(*) AS files, COALESCE(SUM(chunk_count), 0) AS chunks,
                   COUNT(DISTINCT repo) AS repositories, MAX(indexed_at) AS indexed_at
            FROM project_corpus_files WHERE project = ?
            """,
            (scope_id,),
        ).fetchone()
        statuses = conn.execute(
            "SELECT status, COUNT(*) AS count FROM project_corpus_files WHERE project = ? GROUP BY status",
            (scope_id,),
        ).fetchall()
        languages = conn.execute(
            """
            SELECT language, COUNT(*) AS count FROM project_corpus_files
            WHERE project = ? AND language != ''
            GROUP BY language ORDER BY count DESC, language LIMIT 20
            """,
            (scope_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "ok": True,
        "project_root": str(root),
        "project_scope": scope_id,
        "files": int(summary["files"] or 0),
        "chunks": int(summary["chunks"] or 0),
        "repositories": int(summary["repositories"] or 0),
        "indexed_at": summary["indexed_at"],
        "by_status": {str(row["status"]): int(row["count"]) for row in statuses},
        "by_language": {str(row["language"]): int(row["count"]) for row in languages},
    }


def recall_from_rag(
    query: str,
    top_k: int = 10,
    min_score: float = 0.3,
    project_root: Path | str | None = None,
) -> dict[str, Any]:
    """Query the unified RAG store, optionally scoped to one corpus root."""
    try:
        from app.application.rag_memory.service import search_rag
    except Exception as exc:
        return {"ok": False, "items": [], "error": f"RAG service unavailable: {exc}"}
    scope_id = project_scope_id(project_root) if project_root else None
    return search_rag(
        query=query, limit=max(1, int(top_k)), min_score=float(min_score), project=scope_id,
    )


def unindex_file(project_root: Path | str, file_path: Path | str) -> dict[str, Any]:
    """Remove one file's source-backed chunks and manifest record."""
    root = Path(project_root).expanduser().resolve()
    target = Path(file_path).expanduser().resolve()
    scope_id = project_scope_id(root)
    legacy_key = legacy_project_key(root)
    try:
        rel = _posix_relative(target, root)
    except ValueError:
        return {"ok": False, "error": "file is outside project_root"}

    try:
        from app.application.rag_memory.service import _conn
    except Exception as exc:
        return {"ok": False, "error": f"RAG service unavailable: {exc}"}

    conn = _conn()
    try:
        pattern = f"[file:{rel}:%"
        cursor = conn.execute(
            """
            DELETE FROM rag_items
            WHERE category = ? AND (project = ? OR project = ?)
              AND (source_uri = ? OR (COALESCE(source_uri, '') = '' AND text LIKE ?))
            """,
            ("code_index", scope_id, legacy_key, rel, pattern),
        )
        deleted = int(cursor.rowcount or 0)
        conn.execute(
            "DELETE FROM project_corpus_files WHERE project = ? AND source_uri = ?",
            (scope_id, rel),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "deleted_chunks": deleted, "file": rel}


def reindex_file(project_root: Path | str, file_path: Path | str) -> dict[str, Any]:
    """Incrementally refresh one file, used by the realtime watcher."""
    root = Path(project_root).expanduser().resolve()
    target = Path(file_path).expanduser().resolve()
    if not target.exists() or not target.is_file():
        return unindex_file(root, target)
    try:
        rel = _posix_relative(target, root)
    except ValueError:
        return {"ok": False, "error": "file is outside project_root"}
    if _path_in_skip_dir(target, root):
        removed = unindex_file(root, target)
        return {**removed, "ok": True, "skipped": True, "reason": "path under skip dir"}

    result = index_project(root, patterns=[rel], replace=False)
    if int(result.get("files_scanned") or 0) == 0:
        removed = unindex_file(root, target)
        return {**removed, "ok": True, "skipped": True, "reason": "file is ignored or unsupported"}
    return {
        "ok": bool(result.get("ok")),
        "chunks_added": int(result.get("chunks_created") or 0),
        "chunks_reused": int(result.get("chunks_reused") or 0),
        "failed": int(result.get("failed_chunks") or 0),
        "file": rel,
        "complete": bool(result.get("complete")),
        "errors": result.get("errors") or [],
    }
