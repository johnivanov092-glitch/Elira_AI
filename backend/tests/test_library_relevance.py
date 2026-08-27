from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.library import runtime  # noqa: E402


def _seed(runtime_db: Path, rows: list[tuple[str, str]]) -> None:
    connection = runtime._conn()
    try:
        for name, preview in rows:
            connection.execute(
                "INSERT INTO files (name, preview, use_in_context) VALUES (?, ?, 1)",
                (name, preview),
            )
        connection.commit()
    finally:
        connection.close()


def test_library_context_prefers_relevance_over_freshness(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime, "SQLITE_DB", tmp_path / "library.db")
    monkeypatch.setattr(runtime, "UPLOADS_DIR", tmp_path / "uploads")
    runtime.init_library_db()
    _seed(runtime.SQLITE_DB, [
        ("openssl-migration.md", "Проект использует устаревший OpenSSL API EVP_MD_CTX_create."),
        ("meeting.txt", "Свежие заметки о бюджете и встрече."),
        ("vacation.txt", "Самый свежий график отпусков."),
    ])

    result = runtime.build_library_context(
        max_files=1,
        query="Где используется устаревший OpenSSL API?",
    )

    assert result["selection"] == "relevance"
    assert result["used_files"] == ["openssl-migration.md"]
    assert "EVP_MD_CTX_create" in result["context"]


def test_library_context_selects_relevant_excerpt_inside_long_preview(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr(runtime, "SQLITE_DB", tmp_path / "library.db")
    monkeypatch.setattr(runtime, "UPLOADS_DIR", tmp_path / "uploads")
    runtime.init_library_db()
    _seed(runtime.SQLITE_DB, [
        ("handbook.md", "Общие сведения. " + ("x" * 4000) + " OPENSSL_TARGET рядом."),
    ])

    result = runtime.build_library_context(
        max_files=1,
        max_chars_per_file=500,
        query="Найди OPENSSL_TARGET",
    )

    assert result["selection"] == "relevance"
    assert "OPENSSL_TARGET" in result["context"]
    assert len(result["context"]) < 700


def test_library_context_falls_back_to_recent_files_without_match(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime, "SQLITE_DB", tmp_path / "library.db")
    monkeypatch.setattr(runtime, "UPLOADS_DIR", tmp_path / "uploads")
    runtime.init_library_db()
    _seed(runtime.SQLITE_DB, [
        ("older.txt", "Первый документ"),
        ("newer.txt", "Второй документ"),
    ])

    result = runtime.build_library_context(max_files=1, query="прикреплённый файл")

    assert result["selection"] == "recent_fallback"
    assert result["used_files"] == ["newer.txt"]
