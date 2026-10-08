"""Library stores original binaries; explicit mutable skills supply derived text."""
from __future__ import annotations

import hashlib
import json

import pytest

from app.application.library import runtime
from app.application.media import resource_store
from app.core import config, data_files


@pytest.fixture
def library(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(runtime, "SQLITE_DB", data / "library.db")
    monkeypatch.setattr(runtime, "UPLOADS_DIR", data / "uploads")
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(data_files, "DATA_DIR", data)
    monkeypatch.setattr("requests.sessions.Session.request",
                        lambda *a, **k: pytest.fail("Library must not process binaries over HTTP"))
    runtime.init_library_db()
    return tmp_path


@pytest.mark.parametrize("filename,skill", [
    ("voice.ogg", "audio-transcribe"), ("clip.mp4", "audio-transcribe"),
    ("book.pdf", "document-read"), ("table.docx", "document-read"),
    ("book.xlsx", "document-read"), ("old.xls", "document-read"),
    ("deck.pptx", "document-read"), ("scan.png", "ocr"),
])
def test_binary_ingestion_toggle_read_preserve_original_and_do_not_process(library, filename, skill):
    original = b"opaque source bytes; no automatic extraction"
    added = runtime.add_file_contents(filename=filename, contents=original, use_in_context=True)
    assert added["ok"] and added["status"] == "not_processed"
    assert added["content_chars"] == added["preview_len"] == 0
    assert runtime.extract_preview(filename, original) == ""
    assert runtime.toggle_context(added["id"], enabled=True)["status"] == "not_processed"
    assert runtime.build_library_context(query="Этот файл из библиотеки")["context"] == ""

    result = runtime.read_library_file(added["id"])
    assert result["ok"] is False and result["error"] == "processing_required"
    assert result["skill"] == skill
    assert str(runtime.UPLOADS_DIR) not in json.dumps(result)
    ref = result["resource"]
    from app.application.code_agent.tools._memory import tool_library
    from app.application.code_agent.tools._resources import tool_resource_materialize
    shown = tool_library(action="read", id=added["id"])
    assert shown["error"] == "processing_required" and shown["skill"] == skill
    assert shown["resource"]["resource_id"] in shown["text"]
    assert str(runtime.UPLOADS_DIR) not in shown["text"]
    record = resource_store.get_record(ref["resource_id"])
    assert record is not None and record.sha256 == hashlib.sha256(original).hexdigest()
    assert resource_store.read_bytes(record) == original
    project = library / "project"
    project.mkdir()
    materialized = tool_resource_materialize(project, shown["resource"]["resource_id"], filename)
    assert materialized["ok"]
    assert (project / filename).read_bytes() == original
    assert runtime.list_files()["count"] == 1


@pytest.mark.parametrize("damage", ["changed", "outside", "missing", "too_large"])
def test_unavailable_raw_file_is_not_exposed_as_a_resource(library, monkeypatch, damage):
    original = b"original"
    added = runtime.add_file_contents(filename="scan.pdf", contents=original)
    connection = runtime._conn()
    try:
        row = connection.execute("SELECT stored_path FROM files WHERE id=?", (added["id"],)).fetchone()
        from pathlib import Path
        source = Path(row["stored_path"])
        if damage == "changed":
            source.write_bytes(b"changed")
        elif damage == "outside":
            source = library / "outside.pdf"
            source.write_bytes(original)
            connection.execute("UPDATE files SET stored_path=? WHERE id=?", (str(source), added["id"]))
            connection.commit()
        elif damage == "missing":
            source.unlink()
        else:
            monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1)
    finally:
        connection.close()
    result = runtime.read_library_file(added["id"])
    assert result["error"] == "resource_unavailable"
    assert "resource" not in result
    assert str(source) not in json.dumps(result)


@pytest.mark.parametrize("full_text", [True, False])
def test_existing_extracted_document_remains_searchable_and_readable(library, full_text):
    original = b"legacy PDF original"
    added = runtime.add_file_contents(filename="legacy.pdf", contents=original)
    text = "LEGACY_DOCUMENT_TARGET — ранее извлечённый текст"
    connection = runtime._conn()
    try:
        connection.execute("UPDATE files SET content=?, preview=?, extraction_status='ready' WHERE id=?",
                           (text if full_text else "", text, added["id"]))
        connection.commit()
    finally:
        connection.close()
    duplicate = runtime.add_file_contents(filename="legacy.pdf", contents=original)
    assert duplicate["id"] == added["id"]
    assert runtime.read_library_file(added["id"])["text"] == text
    assert runtime.search_files("LEGACY_DOCUMENT_TARGET")["items"][0]["id"] == added["id"]
    assert text in runtime.build_library_context(query="LEGACY_DOCUMENT_TARGET")["context"]


def test_skill_transcript_is_indexed_as_plain_text_without_stt(library):
    text = "[00:00:17] TRANSCRIPT_TARGET — расшифровка с таймкодом.\n"
    added = runtime.add_file_contents(filename="transcript.txt", contents=text.encode("utf-8"))
    assert added["status"] == "ready"
    assert runtime.read_library_file(added["id"])["text"] == text
    assert runtime.search_files("TRANSCRIPT_TARGET")["items"][0]["id"] == added["id"]
