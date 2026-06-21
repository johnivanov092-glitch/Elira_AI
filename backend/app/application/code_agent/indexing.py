"""Project code indexing for the code-agent: chunk source files into RAG and
recall them. Extracted verbatim from agent_loop.py (no behaviour change) to
keep the runtime module focused on the agent loop itself.

Public surface is re-exported from agent_loop for backward compatibility.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterator

from app.application.projects.scope import legacy_project_key, project_scope_id

logger = logging.getLogger(__name__)

PROJECT_PROMPT_FILENAME = ".elira/agent.md"

DEFAULT_INDEX_PATTERNS = [
    "**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx",
    "**/*.md", "**/*.rs", "**/*.go", "**/*.java", "**/*.cpp", "**/*.c", "**/*.h",
]
INDEX_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".pytest_cache", ".mypy_cache", "data"}
INDEX_CHUNK_LINES = 80
INDEX_CHUNK_OVERLAP = 10
INDEX_MAX_FILE_BYTES = 200_000  # skip files larger than 200 KB
INDEX_MAX_TOTAL_CHUNKS = 5000


def _chunk_file(file_path: Path, project_root: Path) -> Iterator[tuple[str, int, int]]:
    """Yield (text, start_line, end_line) chunks for one file. start_line is 1-based."""
    try:
        size = file_path.stat().st_size
    except OSError:
        return
    if size > INDEX_MAX_FILE_BYTES:
        return
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception:
        return
    if not lines:
        return
    rel = str(file_path.relative_to(project_root)).replace("\\", "/")
    step = max(1, INDEX_CHUNK_LINES - INDEX_CHUNK_OVERLAP)
    for start in range(0, len(lines), step):
        end = min(start + INDEX_CHUNK_LINES, len(lines))
        if end <= start:
            break
        chunk_text = "".join(lines[start:end])
        if not chunk_text.strip():
            continue
        header = f"[file:{rel}:{start + 1}-{end}]\n"
        yield header + chunk_text, start + 1, end
        if end >= len(lines):
            break


def _iter_project_files(project_root: Path, patterns: list[str]) -> Iterator[Path]:
    root = project_root.resolve()
    for pattern in patterns:
        for match in root.glob(pattern):
            if not match.is_file():
                continue
            if any(part in INDEX_SKIP_DIRS for part in match.parts):
                continue
            yield match


def index_project(
    project_root: Path | str,
    *,
    patterns: list[str] | None = None,
    replace: bool = True,
) -> dict[str, Any]:
    """Walk the project, chunk source files, write each chunk into RAG.
    If replace=True, prior code_index entries are nuked first.

    Returns counts of files / chunks processed and any per-file errors.
    """
    root = Path(project_root).resolve()
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": f"project_root does not exist: {root}"}

    try:
        from app.application.rag_memory.service import add_to_rag, _conn
    except Exception as exc:
        return {"ok": False, "error": f"RAG service unavailable: {exc}"}

    scope_id = project_scope_id(root)
    legacy_key = legacy_project_key(root)

    if replace:
        try:
            conn = _conn()
            try:
                # Only clear THIS project's code_index entries, not all
                # projects globally. Older rows without a project tag
                # are also cleared so a fresh re-index gets a clean slate.
                conn.execute(
                    """
                    DELETE FROM rag_items
                    WHERE category = ?
                      AND (project = ? OR project = ? OR COALESCE(project, '') = '')
                    """,
                    ("code_index", scope_id, legacy_key),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Failed to nuke prior code_index entries: %s", exc)

    pats = patterns or DEFAULT_INDEX_PATTERNS
    seen_files: set[Path] = set()
    files_processed = 0
    chunks_indexed = 0
    failed_chunks = 0
    errors: list[str] = []

    for file_path in _iter_project_files(root, pats):
        if file_path in seen_files:
            continue
        seen_files.add(file_path)
        files_processed += 1
        for chunk_text, _start, _end in _chunk_file(file_path, root):
            if chunks_indexed >= INDEX_MAX_TOTAL_CHUNKS:
                errors.append(f"Stopped at INDEX_MAX_TOTAL_CHUNKS={INDEX_MAX_TOTAL_CHUNKS}")
                break
            try:
                result = add_to_rag(
                    text=chunk_text,
                    category="code_index",
                    importance=4,
                    project=scope_id,
                )
                if result.get("ok"):
                    chunks_indexed += 1
                else:
                    failed_chunks += 1
            except Exception as exc:
                failed_chunks += 1
                logger.debug("indexing chunk failed for %s: %s", file_path, exc)
        if chunks_indexed >= INDEX_MAX_TOTAL_CHUNKS:
            break

    return {
        "ok": True,
        "files_processed": files_processed,
        "chunks_indexed": chunks_indexed,
        "failed_chunks": failed_chunks,
        "patterns": pats,
        "errors": errors,
    }


def recall_from_rag(
    query: str,
    top_k: int = 10,
    min_score: float = 0.3,
    project_root: Path | str | None = None,
) -> dict[str, Any]:
    """Thin wrapper for the UI to query RAG without going through the agent."""
    try:
        from app.application.rag_memory.service import search_rag
    except Exception as exc:
        return {"ok": False, "items": [], "error": f"RAG service unavailable: {exc}"}
    scope_id = project_scope_id(project_root) if project_root else None
    return search_rag(
        query=query,
        limit=max(1, int(top_k)),
        min_score=float(min_score),
        project=scope_id,
    )


def unindex_file(project_root: Path | str, file_path: Path | str) -> dict[str, Any]:
    """Remove RAG chunks belonging to a single file.

    Chunks emitted by `index_project` carry a `[file:<rel-path>:<a>-<b>]\\n`
    header — we LIKE-match that header so we delete only this file's
    chunks, scoped to the project they belong to.
    """
    root = Path(project_root).resolve()
    target = Path(file_path).resolve()
    scope_id = project_scope_id(root)
    legacy_key = legacy_project_key(root)
    try:
        rel = str(target.relative_to(root)).replace("\\", "/")
    except ValueError:
        return {"ok": False, "error": "file is outside project_root"}

    try:
        from app.application.rag_memory.service import _conn
    except Exception as exc:
        return {"ok": False, "error": f"RAG service unavailable: {exc}"}

    pattern = f"[file:{rel}:%"
    conn = _conn()
    try:
        cur = conn.execute(
            """
            DELETE FROM rag_items
            WHERE category = ?
              AND (project = ? OR project = ? OR COALESCE(project, '') = '')
              AND text LIKE ?
            """,
            ("code_index", scope_id, legacy_key, pattern),
        )
        deleted = cur.rowcount or 0
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "deleted_chunks": deleted, "file": rel}


def reindex_file(project_root: Path | str, file_path: Path | str) -> dict[str, Any]:
    """Replace a single file's chunks in RAG.

    Used by the realtime file watcher. Equivalent to calling
    `unindex_file` then re-chunking + adding only this file. Cheaper
    than rebuilding the full project index when one source file
    changes during a coding session.
    """
    root = Path(project_root).resolve()
    target = Path(file_path).resolve()
    if not target.is_file():
        # Treat as removal — caller may not have intended this but
        # honoring delete-after-rename semantics is the right default.
        return unindex_file(root, target)
    if not target.exists():
        return {"ok": False, "error": f"file does not exist: {target}"}
    try:
        target.relative_to(root)
    except ValueError:
        return {"ok": False, "error": "file is outside project_root"}
    if any(part in INDEX_SKIP_DIRS for part in target.parts):
        return {"ok": True, "skipped": True, "reason": "path under skip dir"}

    try:
        from app.application.rag_memory.service import add_to_rag
    except Exception as exc:
        return {"ok": False, "error": f"RAG service unavailable: {exc}"}

    unindex_file(root, target)  # blow away the old chunks first
    scope_id = project_scope_id(root)
    added = 0
    failed = 0
    for chunk_text, _start, _end in _chunk_file(target, root):
        try:
            result = add_to_rag(
                text=chunk_text,
                category="code_index",
                importance=4,
                project=scope_id,
            )
            if result.get("ok"):
                added += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return {"ok": True, "chunks_added": added, "failed": failed, "file": str(target.relative_to(root)).replace("\\", "/")}
