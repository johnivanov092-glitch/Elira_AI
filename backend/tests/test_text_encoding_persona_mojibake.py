from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.persona import store as persona_store  # noqa: E402
from app.utils.text_encoding import (  # noqa: E402
    looks_like_mojibake,
    mojibake_score,
    repair_mojibake_payload,
    repair_mojibake_text,
)


def cp1251_mojibake(value: str) -> str:
    return value.encode("utf-8").decode("cp1251")


class TextEncodingPersonaMojibakeTest(unittest.TestCase):
    def test_repairs_cp1251_mojibake_text(self) -> None:
        readable = (
            "\u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430 "
            "\u0438 \u043f\u043e\u043d\u044f\u0442\u043d\u044b\u0435 "
            "\u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0435 "
            "\u0448\u0430\u0433\u0438."
        )
        broken = cp1251_mojibake(readable)

        self.assertTrue(looks_like_mojibake(broken))
        self.assertGreater(mojibake_score(broken), 0)
        self.assertEqual(repair_mojibake_text(broken), readable)

    def test_repairs_payload_recursively(self) -> None:
        readable = (
            "\u0421\u0442\u0440\u0443\u043a\u0442\u0443\u0440"
            "\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0439 "
            "\u0438 \u044f\u0441\u043d\u044b\u0439 "
            "\u043e\u0442\u0432\u0435\u0442."
        )
        payload = {
            "summary": cp1251_mojibake(readable),
            "nested": [{"value": cp1251_mojibake(readable)}],
        }

        repaired = repair_mojibake_payload(payload)

        self.assertEqual(repaired["summary"], readable)
        self.assertEqual(repaired["nested"][0]["value"], readable)

    def test_preserves_readable_russian_text(self) -> None:
        readable = (
            "\u042d\u043b\u0438\u0440\u0430 "
            "\u044d\u0432\u043e\u043b\u044e\u0446"
            "\u0438\u043e\u043d\u0438\u0440\u043e\u0432\u0430\u043b\u0430."
        )

        self.assertFalse(looks_like_mojibake(readable))
        self.assertEqual(repair_mojibake_text(readable), readable)

    def test_persona_json_helpers_normalize_text(self) -> None:
        readable = (
            "\u041f\u0440\u043e\u0444\u0438\u043b\u0438 "
            "\u2014 \u044d\u0442\u043e \u0440\u0435\u0436\u0438\u043c"
            "\u044b \u043f\u043e\u0432\u0435\u0434\u0435\u043d\u0438"
            "\u044f \u043e\u0434\u043d\u043e\u0439 Elira."
        )
        broken = cp1251_mojibake(readable)

        dumped = persona_store.json_dumps({"summary": broken})
        loaded = persona_store.json_loads(dumped, {})

        self.assertIn(readable, dumped)
        self.assertEqual(loaded["summary"], readable)

    def test_persona_db_repair_updates_mojibake_rows(self) -> None:
        readable = (
            "\u0421\u0442\u0440\u0443\u043a\u0442\u0443\u0440"
            "\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0439 "
            "\u0438 \u044f\u0441\u043d\u044b\u0439 "
            "\u043e\u0442\u0432\u0435\u0442."
        )
        broken = cp1251_mojibake(readable)
        temp_dir = Path(tempfile.mkdtemp(prefix="persona-encoding-test-"))
        db_path = temp_dir / "persona.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "CREATE TABLE persona_versions ("
                "source TEXT, payload_json TEXT, diff_summary TEXT)"
            )
            conn.execute(
                "INSERT INTO persona_versions "
                "(source, payload_json, diff_summary) VALUES (?, ?, ?)",
                (
                    json.dumps({"summary": broken}, ensure_ascii=False),
                    "{}",
                    broken,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        original_connect = persona_store.connect

        def connect_test_db() -> sqlite3.Connection:
            test_conn = sqlite3.connect(db_path)
            test_conn.row_factory = sqlite3.Row
            return test_conn

        try:
            persona_store.connect = connect_test_db
            result = persona_store.repair_persona_encoding_in_db()
        finally:
            persona_store.connect = original_connect

        try:
            check = sqlite3.connect(db_path)
            row = check.execute(
                "SELECT source, diff_summary FROM persona_versions"
            ).fetchone()
        finally:
            check.close()
            shutil.rmtree(temp_dir)

        self.assertEqual(result, {"ok": True, "updated_rows": 1})
        self.assertIn(readable, row[0])
        self.assertEqual(row[1], readable)

    def test_project_source_files_do_not_contain_mojibake(self) -> None:
        bad_files = []
        source_roots = (
            (ROOT / "backend" / "app", {".py"}),
            (ROOT / "frontend" / "src", {".css", ".js", ".jsx", ".ts", ".tsx"}),
            (ROOT / "src-tauri" / "src", {".py", ".rs"}),
        )
        source_files = (
            ROOT / "src-tauri" / "main.rs",
            ROOT / "src-tauri" / "Cargo.toml",
            ROOT / "src-tauri" / "tauri.conf.json",
        )

        for root, suffixes in source_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file() and path.suffix in suffixes:
                    text = path.read_text(encoding="utf-8")
                    score = mojibake_score(text)
                    if score:
                        bad_files.append(f"{path.relative_to(ROOT)}:{score}")

        for path in source_files:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            score = mojibake_score(text)
            if score:
                bad_files.append(f"{path.relative_to(ROOT)}:{score}")

        self.assertEqual(bad_files, [])

    def test_live_coordination_docs_do_not_contain_mojibake(self) -> None:
        bad_files = []
        for relative_path in (
            "docs/ACTUAL_WORK.md",
            "docs/WORKPLAN_CODEX_CLAUDE.md",
        ):
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            score = mojibake_score(text)
            if score:
                bad_files.append(f"{relative_path}:{score}")

        self.assertEqual(bad_files, [])


if __name__ == "__main__":
    unittest.main()
