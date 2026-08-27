from __future__ import annotations

import hashlib
import io
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.application.file_extract.runtime import _AUDIO_EXTS, is_extract_error
from app.core.config import DATA_DIR, UPLOAD_DIR
from app.infrastructure.db.connection import connect_sqlite

logger = logging.getLogger(__name__)

SQLITE_DB = DATA_DIR / "library.db"
UPLOADS_DIR = UPLOAD_DIR

_SEARCH_WORD_RE = re.compile(r"[^\W_]{3,}", re.UNICODE)
_SEARCH_STOP_WORDS = frozenset({
    "about", "attached", "document", "file", "from", "into", "please", "that",
    "the", "this", "what", "with", "будет", "были", "ваш", "весь", "где",
    "для", "документ", "есть", "ещё", "какие", "который", "мне", "можно",
    "найди", "нужно", "покажи", "про", "проанализируй", "сделай", "скажи",
    "этот", "файл", "файле", "файлы", "через", "что", "это",
})
_LIBRARY_INTENT_RE = re.compile(
    r"\b(?:library|библиотек\w*|вложен\w*|прикрепл[её]н\w*|"
    r"(?:этот|данн\w*|выбранн\w*|активн\w*)\s+(?:документ\w*|файл\w*)|"
    r"(?:документ\w*|файл\w*)\s+(?:из\s+)?(?:library|библиотек\w*))\b",
    re.IGNORECASE,
)

# Routing per user decision: images always go to the vision model (:8004),
# which returns a text description we store as the preview. Document OCR
# (scanned PDFs) goes to the server OCR service (:8002) with local pytesseract
# as a fallback. See app.infrastructure.llm.vision_ocr.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}

_MAX_PREVIEW_CHARS = 12000
_MAX_LIBRARY_CONTENT_CHARS = 1_000_000
_DEFAULT_READ_CHARS = 8000
_MAX_READ_CHARS = 10000

TEXT_EXTS = {
    ".txt",
    ".md",
    ".json",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".py",
    ".css",
    ".html",
    ".yml",
    ".yaml",
    ".xml",
    ".csv",
    ".log",
    ".ini",
    ".toml",
    ".bas",
    ".vbs",
    ".vba",
    ".cls",
    ".frm",
    ".rsc",
    ".bat",
    ".cmd",
    ".ps1",
    ".sh",
    ".sql",
    ".rb",
    ".php",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".cs",
    ".go",
    ".rs",
}


def _conn() -> sqlite3.Connection:
    return connect_sqlite(SQLITE_DB, row_factory=sqlite3.Row, journal_mode=None)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_library_db() -> None:
    SQLITE_DB.parent.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                size INTEGER DEFAULT 0,
                type TEXT DEFAULT 'unknown',
                preview TEXT DEFAULT '',
                use_in_context INTEGER DEFAULT 1,
                source TEXT DEFAULT 'upload',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _ensure_column(conn, "files", "stored_path", "stored_path TEXT DEFAULT ''")
        _ensure_column(conn, "files", "sha256", "sha256 TEXT DEFAULT ''")
        _ensure_column(conn, "files", "content", "content TEXT DEFAULT ''")
        _ensure_column(
            conn,
            "files",
            "extraction_status",
            "extraction_status TEXT DEFAULT 'legacy_preview'",
        )
        _ensure_column(conn, "files", "last_used_at", "last_used_at TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lib_ctx ON files(use_in_context)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lib_sha256 ON files(sha256)")
        try:
            fts_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(files_fts)").fetchall()
            }
            if fts_columns and not {"name", "content", "preview"}.issubset(fts_columns):
                conn.execute("DROP TRIGGER IF EXISTS files_fts_ai")
                conn.execute("DROP TRIGGER IF EXISTS files_fts_ad")
                conn.execute("DROP TRIGGER IF EXISTS files_fts_au")
                conn.execute("DROP TABLE files_fts")
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5("
                "name, content, preview, content='files', content_rowid='id', "
                "tokenize='unicode61')"
            )
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS files_fts_ai AFTER INSERT ON files BEGIN "
                "INSERT INTO files_fts(rowid, name, content, preview) "
                "VALUES (new.id, new.name, new.content, new.preview); END"
            )
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS files_fts_ad AFTER DELETE ON files BEGIN "
                "INSERT INTO files_fts(files_fts, rowid, name, content, preview) "
                "VALUES ('delete', old.id, old.name, old.content, old.preview); END"
            )
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS files_fts_au "
                "AFTER UPDATE OF name, content, preview ON files BEGIN "
                "INSERT INTO files_fts(files_fts, rowid, name, content, preview) "
                "VALUES ('delete', old.id, old.name, old.content, old.preview); "
                "INSERT INTO files_fts(rowid, name, content, preview) "
                "VALUES (new.id, new.name, new.content, new.preview); END"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS library_meta ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            index_version = conn.execute(
                "SELECT value FROM library_meta WHERE key = 'fts_version'"
            ).fetchone()
            if index_version is None or str(index_version["value"]) != "1":
                conn.execute("INSERT INTO files_fts(files_fts) VALUES ('rebuild')")
                conn.execute(
                    "INSERT INTO library_meta(key, value) VALUES ('fts_version', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
                )
        except sqlite3.OperationalError as exc:
            logger.warning("library FTS5 index unavailable: %s", exc)
        conn.commit()
    finally:
        conn.close()


def safe_disk_name(filename: str, data: bytes) -> str:
    original = Path(filename or "unknown").name
    stem = Path(original).stem or "file"
    suffix = Path(original).suffix
    safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in stem)[:80] or "file"
    digest = hashlib.sha256(data).hexdigest()[:12]
    return f"{safe_stem}_{digest}{suffix}"


def _describe_image_preview(filename: str, contents: bytes) -> str:
    """Images route to the vision model (:8004); the returned text description
    becomes the preview. Empty string if vision is disabled/unreachable."""
    try:
        from app.infrastructure.llm.vision_ocr import describe_image

        description = describe_image(filename, contents)
    except Exception as exc:
        logger.warning("vision preview failed for %s: %s", filename, exc)
        return ""
    return (description or "")[:12000]


def _ocr_pdf_preview(filename: str, contents: bytes) -> str:
    """Scanned-PDF text: server OCR (:8002) first, local pytesseract fallback.
    Empty string if neither produces text."""
    try:
        from app.infrastructure.llm.vision_ocr import ocr_document

        text = ocr_document(filename, contents)
        if text and text.strip():
            return text[:12000]
    except Exception as exc:
        logger.warning("server OCR failed for %s: %s", filename, exc)
    try:
        from app.application.pdf.runtime import _try_ocr

        local = _try_ocr(contents, 12000)
        if local and local.strip():
            return local[:12000]
    except Exception as exc:
        logger.warning("local OCR fallback failed for %s: %s", filename, exc)
    return ""


def _extract_via_file_extract(filename: str, contents: bytes) -> str:
    """Delegate to the composer's extractor (file_extract.extract_file) for
    formats the Library shares with it but doesn't parse itself: audio (whisper
    STT), legacy .xls (xlrd), .pptx (python-pptx). One implementation, no drift.
    Empty string on failure."""
    try:
        from app.application.file_extract.runtime import extract_file

        text = extract_file(filename, contents).get("text") or ""
    except Exception as exc:
        logger.warning("library extract via file_extract failed for %s: %s", filename, exc)
        return ""
    return "" if is_extract_error(text) else text[:12000]


def extract_preview(filename: str, contents: bytes) -> str:
    ext = Path(filename).suffix.lower()
    preview = ""
    if ext in TEXT_EXTS:
        return contents.decode("utf-8", errors="replace")[:12000]
    if ext in IMAGE_EXTS:
        return _describe_image_preview(filename, contents)
    # audio (whisper) / legacy .xls (xlrd) / .pptx — reuse the composer extractor.
    if ext in _AUDIO_EXTS or ext in (".xls", ".pptx"):
        return _extract_via_file_extract(filename, contents)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(contents))
            parts = [(page.extract_text() or "") for page in reader.pages[:20]]
            preview = "\n".join(parts)[:12000]
        except Exception:
            preview = ""
        # Scanned PDF: little/no embedded text → route to OCR.
        if len(preview.strip()) < 100:
            ocr_preview = _ocr_pdf_preview(filename, contents)
            if ocr_preview.strip():
                preview = ocr_preview
    elif ext in (".docx", ".doc"):
        try:
            from docx import Document

            doc = Document(io.BytesIO(contents))
            preview = "\n".join(p.text for p in doc.paragraphs if p.text.strip())[:12000]
        except Exception:
            preview = ""
    elif ext in (".xlsx", ".xlsm"):
        try:
            from openpyxl import load_workbook

            wb = load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
            parts = []
            for sheet in wb.sheetnames[:3]:
                ws = wb[sheet]
                parts.append(f"=== {sheet} ===")
                for row in ws.iter_rows(max_row=100, values_only=True):
                    parts.append(" | ".join(str(c) if c is not None else "" for c in row))
            preview = "\n".join(parts)[:12000]
            wb.close()
        except Exception:
            preview = ""
    return preview


def extract_full_text(filename: str, contents: bytes) -> str:
    """Extract reusable Library text once; request prompts receive only excerpts.

    The one-million-character storage cap is a physical ingestion boundary, not
    a per-request reading limit. ``read_library_file`` exposes the stored text in
    small repeatable pages so the model can consume the complete document.
    """
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTS:
        return _describe_image_preview(filename, contents)[:_MAX_LIBRARY_CONTENT_CHARS]
    try:
        from app.application.file_extract.runtime import extract_file

        result = extract_file(
            filename,
            contents,
            max_chars=_MAX_LIBRARY_CONTENT_CHARS,
        )
        if not isinstance(result, dict) or result.get("ok") is False:
            return ""
        text = str(result.get("text") or "")
        return "" if is_extract_error(text) else text[:_MAX_LIBRARY_CONTENT_CHARS]
    except Exception as exc:
        logger.warning("library full-text extraction failed for %s: %s", filename, exc)
        return ""


def read_disk_preview(stored_path: str, max_chars: int) -> str:
    if not stored_path:
        return ""
    try:
        return Path(stored_path).read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:
        return ""


def _extraction_status(content: str, preview: str) -> str:
    if content:
        return "capped" if len(content) >= _MAX_LIBRARY_CONTENT_CHARS else "ready"
    return "preview_only" if preview else "failed"


def _public_row(row: sqlite3.Row, *, active_key: bool) -> dict[str, Any]:
    content_chars = int(row["content_chars"] or 0)
    preview_chars = int(row["preview_chars"] or 0)
    result = {
        "id": int(row["id"]),
        "name": str(row["name"] or ""),
        "size": int(row["size"] or 0),
        "type": str(row["type"] or "unknown"),
        "source": str(row["source"] or "upload"),
        "created_at": row["created_at"],
        "content_chars": content_chars,
        "preview_chars": preview_chars,
        "status": str(row["extraction_status"] or "legacy_preview"),
        "last_used_at": row["last_used_at"],
    }
    if active_key:
        result["active"] = bool(row["use_in_context"])
    else:
        result["use_in_context"] = int(bool(row["use_in_context"]))
    return result


def _public_rows(*, active_key: bool) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, name, size, type, source, use_in_context, created_at, "
            "extraction_status, last_used_at, LENGTH(content) AS content_chars, "
            "LENGTH(preview) AS preview_chars FROM files "
            "ORDER BY created_at DESC, id DESC"
        ).fetchall()
    finally:
        conn.close()
    return [_public_row(row, active_key=active_key) for row in rows]


def list_files() -> dict[str, Any]:
    items = _public_rows(active_key=False)
    return {"ok": True, "items": items, "count": len(items)}


def add_file_contents(
    *,
    filename: str,
    contents: bytes,
    content_type: str | None = None,
    use_in_context: bool = True,
    source: str = "upload",
) -> dict[str, Any]:
    filename = filename or "unknown"
    if not contents:
        return {"ok": False, "error": "empty file"}

    sha256 = hashlib.sha256(contents).hexdigest()
    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT id, name, preview, content, use_in_context FROM files "
            "WHERE sha256 = ? ORDER BY id DESC LIMIT 1",
            (sha256,),
        ).fetchone()
    finally:
        conn.close()

    if existing is not None and str(existing["content"] or ""):
        active = bool(existing["use_in_context"]) or bool(use_in_context)
        conn = _conn()
        try:
            conn.execute(
                "UPDATE files SET use_in_context = ? WHERE id = ?",
                (1 if active else 0, int(existing["id"])),
            )
            conn.commit()
        finally:
            conn.close()
        content = str(existing["content"] or "")
        preview = str(existing["preview"] or "")
        return {
            "ok": True,
            "id": int(existing["id"]),
            "name": str(existing["name"] or filename),
            "preview_len": len(preview),
            "content_chars": len(content),
            "status": _extraction_status(content, preview),
            "active": active,
            "duplicate": True,
        }

    content = extract_full_text(filename, contents)
    preview = content[:_MAX_PREVIEW_CHARS]
    if not preview:
        preview = extract_preview(filename, contents)[:_MAX_PREVIEW_CHARS]
    status = _extraction_status(content, preview)

    if existing is not None:
        active = bool(existing["use_in_context"]) or bool(use_in_context)
        conn = _conn()
        try:
            conn.execute(
                "UPDATE files SET preview = ?, content = ?, extraction_status = ?, "
                "use_in_context = ? WHERE id = ?",
                (preview, content, status, 1 if active else 0, int(existing["id"])),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "ok": True,
            "id": int(existing["id"]),
            "name": str(existing["name"] or filename),
            "preview_len": len(preview),
            "content_chars": len(content),
            "status": status,
            "active": active,
            "duplicate": True,
        }

    disk_name = safe_disk_name(filename, contents)
    disk_path = UPLOADS_DIR / disk_name
    disk_path.write_bytes(contents)

    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO files (name, size, type, preview, content, extraction_status, "
            "use_in_context, source, stored_path, sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                filename,
                len(contents),
                content_type or Path(filename).suffix.lower() or "unknown",
                preview,
                content,
                status,
                1 if use_in_context else 0,
                source or "upload",
                str(disk_path),
                sha256,
            ),
        )
        file_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "id": file_id,
        "name": filename,
        "preview_len": len(preview),
        "content_chars": len(content),
        "status": status,
        "active": bool(use_in_context),
    }


def _hydrate_file(file_id: int) -> sqlite3.Row | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (int(file_id),)).fetchone()
    finally:
        conn.close()
    if row is None or str(row["content"] or ""):
        return row

    stored_path = str(row["stored_path"] or "")
    try:
        source = Path(stored_path)
        contents = source.read_bytes() if source.is_file() else b""
    except Exception:
        contents = b""
    content = extract_full_text(str(row["name"] or "unknown"), contents) if contents else ""
    preview = str(row["preview"] or "") or content[:_MAX_PREVIEW_CHARS]
    status = _extraction_status(content, preview)
    conn = _conn()
    try:
        conn.execute(
            "UPDATE files SET preview = ?, content = ?, extraction_status = ? WHERE id = ?",
            (preview, content, status, int(file_id)),
        )
        conn.commit()
        return conn.execute("SELECT * FROM files WHERE id = ?", (int(file_id),)).fetchone()
    finally:
        conn.close()


def toggle_context(file_id: int, *, enabled: bool = True) -> dict[str, Any]:
    row = _hydrate_file(file_id) if enabled else None
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE files SET use_in_context = ? WHERE id = ?",
            (1 if enabled else 0, file_id),
        )
        conn.commit()
    finally:
        conn.close()
    if cur.rowcount == 0:
        return {"ok": False, "error": "file_not_found", "id": file_id}
    content = str(row["content"] or "") if row is not None else ""
    preview = str(row["preview"] or "") if row is not None else ""
    return {
        "ok": True,
        "id": file_id,
        "use_in_context": enabled,
        "status": _extraction_status(content, preview) if enabled else "inactive",
    }


def import_resource(resource_id: str, *, use_in_context: bool = True) -> dict[str, Any]:
    """Copy an explicit durable chat resource into the permanent Library owner."""
    from app.application.media import resource_store
    from app.core.config import MAX_UPLOAD_BYTES

    record = resource_store.get_record(str(resource_id or "").strip())
    if record is None:
        return {"ok": False, "error": "resource_not_found"}
    if record.size > MAX_UPLOAD_BYTES:
        return {"ok": False, "error": "resource_too_large", "max_bytes": MAX_UPLOAD_BYTES}
    try:
        contents = resource_store.read_bytes(record)
    except Exception:
        return {"ok": False, "error": "resource_read_failed"}
    return add_file_contents(
        filename=record.original_name,
        contents=contents,
        content_type=record.content_type,
        use_in_context=use_in_context,
        source="resource",
    )


def delete_file(file_id: int) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT stored_path FROM files WHERE id = ?", (file_id,)).fetchone()
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        remaining_refs = int(conn.execute(
            "SELECT COUNT(*) FROM files WHERE stored_path = ?",
            (str(row["stored_path"] or "") if row else "",),
        ).fetchone()[0])
        conn.commit()
    finally:
        conn.close()

    if row and row["stored_path"] and remaining_refs == 0:
        try:
            path = Path(row["stored_path"])
            if path.exists() and path.is_file():
                path.unlink()
        except Exception:
            pass
    return {"ok": True, "deleted_id": file_id}


def search_files(query: str = "", *, limit: int = 20) -> dict[str, Any]:
    terms = _relevance_terms(query)
    rows = _search_candidate_rows(terms, active_only=False, limit=limit)
    ranked = [(_library_row_score(row, terms), index, row) for index, row in enumerate(rows)]
    if terms:
        ranked = [item for item in ranked if item[0] > 0]
        ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = ranked[:max(1, min(int(limit), 50))]
    items: list[dict[str, Any]] = []
    for score, _, row in selected:
        content = str(row["content"] or row["preview"] or "")
        items.append({
            "id": int(row["id"]),
            "name": str(row["name"] or ""),
            "active": bool(row["use_in_context"]),
            "status": str(row["extraction_status"] or "legacy_preview"),
            "content_chars": len(content),
            "score": score,
            "excerpt": _relevant_excerpt(content, terms, 800),
        })
    return {"ok": True, "items": items, "count": len(items), "query_terms": list(terms)}


def get_context_files() -> dict[str, Any]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, name, preview, stored_path FROM files WHERE use_in_context = 1 AND preview != '' ORDER BY created_at DESC, id DESC LIMIT 10"
        ).fetchall()
    finally:
        conn.close()
    return {"ok": True, "items": [dict(row) for row in rows], "count": len(rows)}


def list_library_files() -> dict[str, Any]:
    files = _public_rows(active_key=True)
    return {"ok": True, "files": files, "count": len(files)}


def read_library_file(
    file_id: int,
    *,
    offset: int = 0,
    limit: int = _DEFAULT_READ_CHARS,
) -> dict[str, Any]:
    """Return one bounded page from a Library document's extracted full text."""
    row = _hydrate_file(int(file_id))
    if row is None:
        return {"ok": False, "error": "file_not_found", "file_id": int(file_id)}
    content = str(row["content"] or row["preview"] or "")
    start = max(0, int(offset))
    page_limit = max(1, min(int(limit), _MAX_READ_CHARS))
    text = content[start:start + page_limit]
    next_offset = start + len(text)
    has_more = next_offset < len(content)
    conn = _conn()
    try:
        conn.execute(
            "UPDATE files SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
            (int(file_id),),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "file_id": int(file_id),
        "name": str(row["name"] or ""),
        "status": str(row["extraction_status"] or "legacy_preview"),
        "offset": start,
        "limit": page_limit,
        "content_chars": len(content),
        "text": text,
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
    }


def set_library_active(filename: str, active: bool) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT id, name FROM files WHERE name = ? ORDER BY id DESC LIMIT 1", (filename,)).fetchone()
        if not row:
            return {"ok": False, "error": f"Файл не найден: {filename}"}
    finally:
        conn.close()
    result = toggle_context(int(row["id"]), enabled=active)
    return {**result, "filename": filename, "active": bool(active)}


def delete_library_file(filename: str) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT id, stored_path FROM files WHERE name = ? ORDER BY id DESC LIMIT 1", (filename,)).fetchone()
        if not row:
            return {"ok": False, "error": f"Файл не найден: {filename}"}
        conn.execute("DELETE FROM files WHERE id = ?", (row["id"],))
        remaining_refs = int(conn.execute(
            "SELECT COUNT(*) FROM files WHERE stored_path = ?",
            (str(row["stored_path"] or ""),),
        ).fetchone()[0])
        conn.commit()
    finally:
        conn.close()

    stored_path = row["stored_path"] or ""
    if stored_path and remaining_refs == 0:
        try:
            path = Path(stored_path)
            if path.exists() and path.is_file():
                path.unlink()
        except Exception:
            pass
    return {"ok": True, "filename": filename}


def _relevance_terms(query: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        word
        for word in _SEARCH_WORD_RE.findall(str(query or "").casefold())
        if word not in _SEARCH_STOP_WORDS
    ))[:64]


def _fts_query(terms: tuple[str, ...]) -> str:
    return " OR ".join(f'"{term}"' for term in terms)


def _search_candidate_rows(
    terms: tuple[str, ...],
    *,
    active_only: bool,
    limit: int,
) -> list[sqlite3.Row]:
    bounded_limit = max(1, min(int(limit), 50))
    conn = _conn()
    try:
        if not terms:
            where = "WHERE use_in_context = 1 " if active_only else ""
            return conn.execute(
                f"SELECT * FROM files {where}ORDER BY created_at DESC, id DESC LIMIT ?",
                (bounded_limit,),
            ).fetchall()
        active_clause = "AND f.use_in_context = 1" if active_only else ""
        try:
            return conn.execute(
                "SELECT f.* FROM files_fts "
                "JOIN files AS f ON f.id = files_fts.rowid "
                f"WHERE files_fts MATCH ? {active_clause} "
                "ORDER BY bm25(files_fts, 8.0, 1.0, 1.0), f.created_at DESC LIMIT ?",
                (_fts_query(terms), bounded_limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS5 may be absent in a custom Python build. Keep the fallback
            # bounded inside SQLite so full documents never fan out into Python.
            clauses = " OR ".join(
                "(name LIKE ? OR content LIKE ? OR preview LIKE ?)" for _ in terms
            )
            params: list[Any] = []
            for term in terms:
                needle = f"%{term}%"
                params.extend((needle, needle, needle))
            active_sql = "use_in_context = 1 AND " if active_only else ""
            params.append(bounded_limit)
            return conn.execute(
                f"SELECT * FROM files WHERE {active_sql}({clauses}) "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
    finally:
        conn.close()


def _library_row_score(row: sqlite3.Row, terms: tuple[str, ...]) -> int:
    name = str(row["name"] or "").casefold()
    content = str(row["content"] or row["preview"] or "").casefold()
    return sum(
        (6 if term in name else 0) + min(3, content.count(term))
        for term in terms
    )


def _relevant_excerpt(content: str, terms: tuple[str, ...], max_chars: int) -> str:
    """Return the best bounded window, not always the beginning of a long preview."""
    limit = max(1, int(max_chars))
    if len(content) <= limit or not terms:
        return content[:limit]
    folded = content.casefold()
    starts = {0}
    for term in terms:
        offset = 0
        while (position := folded.find(term, offset)) >= 0:
            starts.add(max(0, min(position - limit // 4, len(content) - limit)))
            offset = position + len(term)
    best_start = max(
        starts,
        key=lambda start: (
            sum(term in folded[start:start + limit] for term in terms),
            sum(folded[start:start + limit].count(term) for term in terms),
            -start,
        ),
    )
    excerpt = content[best_start:best_start + limit]
    if best_start > 0:
        excerpt = "[…]\n" + excerpt
    if best_start + limit < len(content):
        excerpt += "\n[…]"
    return excerpt


def build_library_context(
    max_files: int = 10,
    max_chars_per_file: int = 2500,
    *,
    query: str = "",
) -> dict[str, Any]:
    """Build a bounded request-specific context from active Library files.

    Active files are candidates, not a permanently injected prefix.  Lexical
    relevance chooses the best previews for the current request; when the query
    has no usable match (for example, "analyse the attached file"), the previous
    freshest-first behaviour remains the deterministic fallback.
    """
    limit = max(1, int(max_files))
    terms = _relevance_terms(query)
    conn = _conn()
    try:
        active_count = int(conn.execute(
            "SELECT COUNT(*) FROM files WHERE use_in_context = 1"
        ).fetchone()[0])
    finally:
        conn.close()
    relevant_rows = _search_candidate_rows(
        terms,
        active_only=True,
        limit=max(limit * 5, 20),
    ) if terms else []
    if relevant_rows:
        ranked = [
            (_library_row_score(row, terms), index, row)
            for index, row in enumerate(relevant_rows)
        ]
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected_rows = [row for _, _, row in ranked]
        selection = "relevance"
    elif _LIBRARY_INTENT_RE.search(str(query or "")):
        selected_rows = _search_candidate_rows((), active_only=True, limit=limit)
        selection = "recent_fallback"
    else:
        selected_rows = []
        selection = "no_match"

    context_parts = []
    used_files = []
    used_ids: list[int] = []
    for row in selected_rows:
        if len(used_files) >= limit:
            break
        if not str(row["content"] or ""):
            hydrated = _hydrate_file(int(row["id"]))
            if hydrated is not None:
                row = hydrated
        suffix = Path(row["name"]).suffix.lower()
        content = str(row["content"] or row["preview"] or "")
        if not content and suffix in TEXT_EXTS:
            content = read_disk_preview(row["stored_path"] or "", max_chars_per_file * 4)
        if not content.strip():
            continue
        content = _relevant_excerpt(content, terms, max_chars_per_file)
        used_files.append(row["name"])
        used_ids.append(int(row["id"]))
        context_parts.append(f"===== FILE: {row['name']} =====\n{content}")

    if used_ids:
        conn = _conn()
        try:
            conn.executemany(
                "UPDATE files SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                [(file_id,) for file_id in used_ids],
            )
            conn.commit()
        finally:
            conn.close()

    return {
        "ok": True,
        "used_files": used_files,
        "active_count": active_count,
        "selection": selection,
        "query_terms": list(terms),
        "context": "\n\n".join(context_parts),
    }


def inject_library_context(message: str, *, query: str | None = None) -> str:
    """Prepend one canonical bounded Library block to an agent request."""
    try:
        result = build_library_context(query=query or message)
    except Exception:
        logger.warning("library context preparation failed", exc_info=True)
        return message
    block = str(result.get("context") or "").strip()
    if not block:
        return message
    used = ", ".join(str(name) for name in (result.get("used_files") or [])) or "Library"
    header = (
        f"Релевантные фрагменты из активной Library ({used}). Используй их, если "
        "они относятся к запросу. Это не обязательно весь документ: для полного "
        "анализа найди file_id через runtime_control(operation='library_search') и "
        "последовательно вызывай operation='library_read' с возвращаемым next_offset."
    )
    return f"{header}\n\n{block}\n\n----- ЗАПРОС ПОЛЬЗОВАТЕЛЯ -----\n{message}"


init_library_db()
