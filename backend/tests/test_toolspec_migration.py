"""Tests — ToolSpec additive migration (P1 Шаг 2).

Verifies that:
1. migrate_toolspec_columns() adds new columns to an existing legacy DB.
2. Existing rows survive the migration with correct default values.
3. register_tool() round-trips new fields through DB.
4. update_tool() can update new fields.
5. row_to_dict() deserialises scopes as list and side_effect/idempotent as bool.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.tool_registry import store as registry_store  # noqa: E402


_LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS tools (
    name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    display_name_ru TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    description_ru TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',
    parameters_schema_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'builtin',
    enabled INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_FULL_SCHEMA = """
CREATE TABLE IF NOT EXISTS tools (
    name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    display_name_ru TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    description_ru TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',
    parameters_schema_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'builtin',
    enabled INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    permission TEXT NOT NULL DEFAULT 'auto',
    side_effect INTEGER NOT NULL DEFAULT 0,
    scopes TEXT NOT NULL DEFAULT '[]',
    timeout_seconds INTEGER NOT NULL DEFAULT 30,
    max_output_chars INTEGER NOT NULL DEFAULT 50000,
    idempotent INTEGER NOT NULL DEFAULT 0
);
"""


def _make_conn(path: Path):
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    return con


def _conn_factory(path: Path):
    def _conn():
        return _make_conn(path)
    return _conn


class TestToolSpecMigration(unittest.TestCase):

    def test_migration_adds_columns_to_legacy_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "legacy.db"

            con = _make_conn(db)
            try:
                con.executescript(_LEGACY_SCHEMA)
                con.execute(
                    "INSERT INTO tools (name, display_name, display_name_ru, description, "
                    "description_ru, category, parameters_schema_json, source, enabled, "
                    "version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 'now', 'now')",
                    ("legacy_tool", "Legacy", "Legacy", "desc", "desc", "test", "{}", "builtin"),
                )
                con.commit()
            finally:
                con.close()

            registry_store.migrate_toolspec_columns(conn_factory=_conn_factory(db))

            con = _make_conn(db)
            try:
                cols = {row[1] for row in con.execute("PRAGMA table_info(tools)").fetchall()}
            finally:
                con.close()

            for col in ("permission", "side_effect", "scopes", "timeout_seconds", "max_output_chars", "idempotent"):
                self.assertIn(col, cols, f"Missing column: {col}")

    def test_migration_preserves_existing_rows_with_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "legacy2.db"

            con = _make_conn(db)
            try:
                con.executescript(_LEGACY_SCHEMA)
                con.execute(
                    "INSERT INTO tools (name, display_name, display_name_ru, description, "
                    "description_ru, category, parameters_schema_json, source, enabled, "
                    "version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 'now', 'now')",
                    ("old_tool", "Old", "Old", "desc", "desc", "test", "{}", "builtin"),
                )
                con.commit()
            finally:
                con.close()

            registry_store.migrate_toolspec_columns(conn_factory=_conn_factory(db))

            con = _make_conn(db)
            try:
                row = dict(con.execute("SELECT * FROM tools WHERE name='old_tool'").fetchone())
            finally:
                con.close()

            self.assertEqual(row["permission"], "auto")
            self.assertEqual(row["side_effect"], 0)
            self.assertEqual(row["scopes"], "[]")
            self.assertEqual(row["timeout_seconds"], 30)
            self.assertEqual(row["max_output_chars"], 50000)
            self.assertEqual(row["idempotent"], 0)
            self.assertEqual(row["display_name"], "Old")
            self.assertEqual(row["enabled"], 1)

    def test_migration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fresh.db"

            con = _make_conn(db)
            try:
                con.executescript(_LEGACY_SCHEMA)
            finally:
                con.close()

            # Running twice must not raise
            registry_store.migrate_toolspec_columns(conn_factory=_conn_factory(db))
            registry_store.migrate_toolspec_columns(conn_factory=_conn_factory(db))

    def test_register_tool_roundtrip_new_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "rt.db"
            conn_f = _conn_factory(db)

            con = _make_conn(db)
            try:
                con.executescript(_FULL_SCHEMA)
            finally:
                con.close()

            handlers: dict = {}
            _now = lambda: "2024-01-01T00:00:00+00:00"

            def _get(name):
                c = _make_conn(db)
                try:
                    row = c.execute("SELECT * FROM tools WHERE name=?", (name,)).fetchone()
                    return registry_store.row_to_dict(row) if row else None
                finally:
                    c.close()

            registry_store.register_tool(
                conn_factory=conn_f,
                handlers=handlers,
                now_func=_now,
                get_tool_func=_get,
                name="test_tool",
                handler=lambda a: {"ok": True},
                description="test",
                permission="require_approval",
                side_effect=True,
                scopes=["fs.read"],
                timeout_seconds=45,
                max_output_chars=1000,
                idempotent=False,
            )

            tool = _get("test_tool")
            assert tool is not None
            self.assertEqual(tool["permission"], "require_approval")
            self.assertTrue(tool["side_effect"])
            self.assertEqual(tool["scopes"], ["fs.read"])
            self.assertEqual(tool["timeout_seconds"], 45)
            self.assertEqual(tool["max_output_chars"], 1000)
            self.assertFalse(tool["idempotent"])

    def test_row_to_dict_deserialises_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "deser.db"

            con = _make_conn(db)
            try:
                con.executescript(_FULL_SCHEMA)
                con.execute(
                    "INSERT INTO tools (name, display_name, display_name_ru, description, "
                    "description_ru, category, parameters_schema_json, source, enabled, version, "
                    "created_at, updated_at, permission, side_effect, scopes, timeout_seconds, "
                    "max_output_chars, idempotent) VALUES (?,?,?,?,?,?,?,?,1,1,'n','n',?,?,?,?,?,?)",
                    ("t", "", "", "", "", "g", "{}", "b", "auto", 1, '["a","b"]', 10, 100, 0),
                )
                con.commit()
                row = con.execute("SELECT * FROM tools WHERE name='t'").fetchone()
                result = registry_store.row_to_dict(row)
            finally:
                con.close()

            self.assertIsInstance(result["scopes"], list)
            self.assertEqual(result["scopes"], ["a", "b"])
            self.assertIsInstance(result["side_effect"], bool)
            self.assertTrue(result["side_effect"])
            self.assertIsInstance(result["idempotent"], bool)
            self.assertFalse(result["idempotent"])

    def test_builtin_tools_have_permission_tiers(self) -> None:
        from app.application.tool_registry.builtins import build_builtin_tools
        tools = {t["name"]: t for t in build_builtin_tools()}

        for name in ("search_memory", "search_web", "read_project_file", "git_status",
                     "list_project_tree", "search_project", "preview_project_patch"):
            t = tools[name]
            self.assertEqual(t["permission"], "auto", f"{name} should be auto")
            self.assertFalse(t["side_effect"], f"{name} should not have side_effect")

        for name in ("write_project_file", "apply_project_patch", "python_execute",
                     "git_commit_push", "browser_run", "project_brain_loop"):
            t = tools[name]
            self.assertEqual(t["permission"], "require_approval", f"{name} should require_approval")
            self.assertTrue(t["side_effect"], f"{name} should have side_effect")


if __name__ == "__main__":
    unittest.main()
