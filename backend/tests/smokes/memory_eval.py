#!/usr/bin/env python3
"""Deterministic memory contract Harness; no LLM, network, or user DB writes.

The suite boots Elira against a temporary ``ELIRA_DATA_DIR`` and exercises the
public memory facade plus the existing encrypted portable backup. Reports use
the same local ``.agent/evals`` convention as live routing evals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
DEFAULT_REPORT_ROOT = REPO_ROOT / ".agent" / "evals" / "memory"


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _write_report(output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    lines = [
        "# Memory eval",
        "",
        f"- Suite: `{report['suite_id']}`",
        f"- Result: **{'PASS' if report['ok'] else 'FAIL'}**",
        f"- Passed: {report['passed']}/{report['total']}",
        "- Runtime: isolated temporary ELIRA_DATA_DIR; LLM/network disabled",
        "",
        "| Case | Status | Details |",
        "|---|---|---|",
    ]
    for name, result in report["cases"].items():
        details = str(result.get("details") or result.get("error") or "").replace("|", "\\|")
        lines.append(f"| `{name}` | {result['status']} | {details} |")
    (output_dir / "report.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def run_memory_contracts(data_dir: Path) -> dict[str, Any]:
    """Run real storage/facade contracts inside an already-empty data root."""
    os.environ["ELIRA_DATA_DIR"] = str(data_dir.resolve())
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))

    # Imports intentionally happen only after ELIRA_DATA_DIR is fixed.
    from app.application import memory, smart_memory
    from app.application.memory.policy import is_authoritative_fact
    from app.application.projects.scope import project_scope_id
    from app.application.rag_memory import service as rag
    from app.infrastructure.secrets import vault
    from app.infrastructure.web_corpus import store as web_store

    rag._get_embedding = lambda _text: None  # type: ignore[assignment]
    web_store._DB_PATH_OVERRIDE = str(data_dir / "web_corpus.sqlite3")

    def clear_durable_memory() -> None:
        smart_memory.clear_all_memories()
        conn = rag._conn()
        try:
            conn.execute("DELETE FROM rag_items")
            conn.execute("DELETE FROM project_corpus_files")
            conn.commit()
        finally:
            conn.close()

    cases: dict[str, dict[str, Any]] = {}

    def execute(name: str, callback: Callable[[], str]) -> None:
        try:
            details = callback()
            cases[name] = {"status": "PASS", "details": details}
        except Exception as exc:  # noqa: BLE001 - every case must reach the report
            cases[name] = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}

    def remember_search_list_delete() -> str:
        clear_durable_memory()
        created = memory.add_fact(
            "Пользователь предпочитает Python для автоматизации.",
            category="preference",
            source="user",
            importance=8,
        )
        _require(created.get("action") == "created", f"unexpected create: {created}")
        found = memory.search_facts("Python автоматизация", limit=5)
        _require(any(item["id"] == created["id"] for item in found["items"]), "search miss")
        listed = memory.list_facts(limit=20)
        _require(any(item["id"] == created["id"] for item in listed["items"]), "list miss")
        deleted = memory.delete_fact(created["id"])
        _require(deleted.get("deleted") == 1, f"delete failed: {deleted}")
        _require(memory.search_facts("Python автоматизация")["count"] == 0, "deleted fact recalled")
        return "remember → search → list → delete"

    def correction_supersedes() -> str:
        clear_durable_memory()
        original = memory.add_fact(
            "Меня зовут Иван.",
            category="preference",
            source="user",
            importance=8,
        )
        corrected = memory.add_fact(
            "Моё имя Пётр.",
            category="preference",
            source="user_correction",
            importance=10,
            replaces_id=original["id"],
        )
        _require(corrected.get("action") == "corrected", f"not corrected: {corrected}")
        _require(corrected.get("id") == original.get("id"), "correction created a conflicting row")
        rows = memory.list_facts(limit=20)["items"]
        _require(len(rows) == 1 and "Пётр" in rows[0]["text"], f"conflicting facts: {rows}")
        _require(rows[0]["source"] == "user_correction", "correction trust source lost")
        return "rephrased value replaced by explicit id; source=user_correction"

    def deduplication() -> str:
        clear_durable_memory()
        first = memory.add_fact("Проект называется Elira.", source="user", importance=8)
        second = memory.add_fact("Проект называется Elira.", source="user", importance=8)
        _require(second.get("action") == "updated", f"dedup action: {second}")
        _require(second.get("id") == first.get("id"), "dedup changed id")
        rows = memory.list_facts(limit=20)["items"]
        _require(len(rows) == 1, f"duplicate rows: {len(rows)}")
        _require(int(rows[0]["importance"]) == 9, "dedup did not bump importance once")
        return "same fact retained one id and importance 8 → 9"

    def volatile_lifecycle() -> str:
        clear_durable_memory()
        volatile = memory.add_fact(
            "Активная модель сейчас Qwen.",
            category="user_fact",
            source="user",
            importance=8,
        )
        _require(volatile.get("category") == "volatile_fact", f"not volatile: {volatile}")
        row = memory.list_facts(limit=5)["items"][0]
        _require(not is_authoritative_fact(row), "volatile fact became authoritative")
        from app.application.smart_memory import store as smart_store

        conn = smart_store.connect_memory_db()
        try:
            conn.execute(
                "UPDATE memories SET updated_at = datetime('now', '-30 days') WHERE id = ?",
                (volatile["id"],),
            )
            conn.commit()
        finally:
            conn.close()
        preview = memory.prune_volatile_facts(max_age_days=7, dry_run=True)
        _require(preview.get("candidates") == 1 and preview.get("pruned") == 0, "dry-run mismatch")
        pruned = memory.prune_volatile_facts(max_age_days=7, dry_run=False)
        _require(pruned.get("pruned") == 1, f"volatile prune failed: {pruned}")
        return "volatile excluded from source truth; age prune dry-run + apply"

    def storage_isolation() -> str:
        clear_durable_memory()
        web_store.cleanup_run("memory-eval-a")
        web_store.cleanup_run("memory-eval-b")
        user_marker = "USER_MEMORY_ONLY_MARKER"
        project_a_marker = "PROJECT_ALPHA_ONLY_MARKER"
        project_b_marker = "PROJECT_BETA_ONLY_MARKER"
        web_marker = "WEB_CORPUS_TRANSIENT_MARKER"
        memory.add_fact(user_marker, category="user_fact", source="user", importance=8)
        scope_a = project_scope_id(data_dir / "project-alpha")
        scope_b = project_scope_id(data_dir / "project-beta")
        rag.add_to_rag(
            f"[file:alpha.py:1-1]\n{project_a_marker}",
            category="code_index",
            project=scope_a,
            source_uri="alpha.py",
            source_hash="alpha-hash",
            metadata={"repo": "alpha", "file": "alpha.py", "language": "python"},
        )
        rag.add_to_rag(
            f"[file:beta.py:1-1]\n{project_b_marker}",
            category="code_index",
            project=scope_b,
            source_uri="beta.py",
            source_hash="beta-hash",
            metadata={"repo": "beta", "file": "beta.py", "language": "python"},
        )
        canonical = f"Temporary untrusted source: {web_marker}"
        doc_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        web_store.store_document(
            run_id="memory-eval-a",
            doc={
                "doc_id": doc_id,
                "url": "https://example.invalid/eval",
                "final_url": "https://example.invalid/eval",
                "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "mime": "text/plain",
                "title": "Memory eval",
                "outline": [],
                "dates": {},
                "tier": "test",
                "nbytes": len(canonical.encode("utf-8")),
                "canonical_text": canonical,
            },
            chunks=[{"chunk_id": 0, "offset": 0, "length": len(canonical), "text": canonical}],
        )

        _require(memory.search_facts(user_marker)["count"] == 1, "user fact missing")
        _require(not rag.search_rag(user_marker, min_score=0.1)["items"], "user fact leaked into RAG")
        a_text = "\n".join(item["text"] for item in rag.search_rag(
            "PROJECT ONLY MARKER", project=scope_a, min_score=0.0, limit=20,
        )["items"])
        _require(project_a_marker in a_text and project_b_marker not in a_text, "project scope leaked")
        _require(web_store.has_documents("memory-eval-a"), "web run A missing")
        _require(not web_store.has_documents("memory-eval-b"), "web run B saw run A")
        _require(not rag.search_rag(web_marker, min_score=0.1)["items"], "web Corpus leaked into RAG")
        _require(memory.search_facts(web_marker)["count"] == 0, "web Corpus leaked into user facts")
        return "user facts global; project RAG scoped; web Corpus run-scoped and separate"

    def encrypted_backup_restore() -> str:
        clear_durable_memory()
        smart_marker = "BACKUP_USER_MEMORY_MARKER"
        rag_marker = "BACKUP_PROJECT_CORPUS_MARKER"
        project_scope = project_scope_id(data_dir / "backup-project")
        memory.add_fact(smart_marker, category="user_fact", source="user", importance=8)
        rag.add_to_rag(
            f"[file:src/main.py:1-1]\n{rag_marker}",
            category="code_index",
            importance=6,
            project=project_scope,
            source_uri="src/main.py",
            source_hash="backup-source-hash",
            metadata={"repo": "backup-project", "commit": "abc123", "language": "python"},
        )
        conn = rag._conn()
        try:
            conn.execute(
                """
                INSERT INTO project_corpus_files (
                    project, source_uri, content_hash, size_bytes, mtime_ns,
                    chunk_count, status, repo, commit_sha, language
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_scope,
                    "src/main.py",
                    "backup-content-hash",
                    128,
                    123456,
                    1,
                    "indexed",
                    "backup-project",
                    "abc123",
                    "python",
                ),
            )
            conn.commit()
        finally:
            conn.close()
        passphrase = "memory-eval-passphrase"
        vault.create(passphrase)
        backup_path = data_dir / "portable-memory-backup.json"
        backed_up = vault.backup(backup_path)
        components = set(backed_up.get("components") or [])
        _require({"smart_memory.db", "rag_memory.db"}.issubset(components), f"memory components: {components}")
        _require("web_corpus.sqlite3" not in components, "temporary web Corpus entered backup")

        clear_durable_memory()
        _require(memory.search_facts(smart_marker)["count"] == 0, "smart clear failed")
        _require(
            not rag.search_rag(rag_marker, project=project_scope, min_score=0.1)["items"],
            "project RAG clear failed",
        )
        vault.lock()
        vault.restore(backup_path)
        vault.unlock(passphrase=passphrase)
        _require(memory.search_facts(smart_marker)["count"] == 1, "smart memory restore failed")
        project_items = rag.search_rag(
            rag_marker,
            project=project_scope,
            min_score=0.1,
            limit=10,
        )["items"]
        restored_chunk = next(
            (item for item in project_items if rag_marker in str(item.get("text") or "")),
            None,
        )
        _require(restored_chunk is not None, "project-scoped RAG restore failed")
        _require(restored_chunk.get("project") == project_scope, "project scope was not restored")
        _require(
            restored_chunk.get("metadata")
            == {"commit": "abc123", "language": "python", "repo": "backup-project"},
            f"project metadata was not restored: {restored_chunk}",
        )
        conn = rag._conn()
        try:
            manifest = conn.execute(
                """
                SELECT content_hash, chunk_count, status, repo, commit_sha, language
                FROM project_corpus_files
                WHERE project = ? AND source_uri = ?
                """,
                (project_scope, "src/main.py"),
            ).fetchone()
        finally:
            conn.close()
        _require(manifest is not None, "Project Corpus manifest was not restored")
        _require(
            tuple(manifest)
            == ("backup-content-hash", 1, "indexed", "backup-project", "abc123", "python"),
            f"Project Corpus manifest changed: {tuple(manifest) if manifest else None}",
        )
        vault.lock()
        return "encrypted bundle restored facts + project chunk/scope/metadata/manifest"

    execute("remember_search_list_delete", remember_search_list_delete)
    execute("correction_supersedes", correction_supersedes)
    execute("deduplication", deduplication)
    execute("volatile_lifecycle", volatile_lifecycle)
    execute("storage_isolation", storage_isolation)
    execute("encrypted_backup_restore", encrypted_backup_restore)

    passed = sum(result["status"] == "PASS" for result in cases.values())
    return {
        "ok": passed == len(cases),
        "passed": passed,
        "total": len(cases),
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", help="Explicit report directory")
    args = parser.parse_args(argv)
    suite_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir).resolve() if args.output_dir else DEFAULT_REPORT_ROOT / suite_id
    with tempfile.TemporaryDirectory(prefix="elira-memory-eval-") as temp_dir:
        data_dir = Path(temp_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        result = run_memory_contracts(data_dir)
    report = {
        "suite_id": suite_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    _write_report(output_dir, report)
    print(json.dumps({"ok": report["ok"], "report": str(output_dir / "report.md")}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
