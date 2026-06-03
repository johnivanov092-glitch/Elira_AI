"""Tests — P9.2A0: approval binding hardening.

Covers:
- canonical_args_digest stability and uniqueness
- args_sha256 migration (additive, idempotent)
- find_approved_approval enforces all 6 binding fields
- legacy approvals with empty digest are never accepted
- executor blocks require_approval tools with empty run_id
- API schemas expose and return run_id
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.monitoring.store import (  # noqa: E402
    canonical_args_digest,
    create_approval,
    find_approved_approval,
    get_connection,
    init_db,
    migrate_approval_args_sha256,
    migrate_approvals_table,
    update_approval_status,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_db() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Path(tmp.name)


def _safe_unlink(db: Path) -> None:
    """Unlink a SQLite db file, ignoring Windows file-locking errors."""
    import gc
    gc.collect()  # flush any lingering SQLite Connection references
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = db.with_suffix(db.suffix + suffix) if suffix else db
        try:
            p.unlink()
        except (PermissionError, FileNotFoundError, OSError):
            pass


def _setup(db: Path) -> None:
    """Create all tables including args_sha256 column."""
    init_db(db)
    migrate_approval_args_sha256(db)


def _make_approval(db: Path, **overrides) -> dict:
    defaults = dict(
        id="ap1",
        tool_name="write_file",
        agent_id="code-agent",
        source="code_agent",
        run_id="run-123",
        project_scope_id="scope-abc",
        args={"path": "/tmp/x.txt", "content": "hello"},
        ttl_seconds=300,
    )
    defaults.update(overrides)
    return create_approval(db, **defaults)


def _find(db: Path, **overrides) -> dict | None:
    base = dict(
        tool_name="write_file",
        agent_id="code-agent",
        source="code_agent",
        run_id="run-123",
        project_scope_id="scope-abc",
        args={"path": "/tmp/x.txt", "content": "hello"},
    )
    base.update(overrides)
    return find_approved_approval(db, **base)


# ── canonical digest ──────────────────────────────────────────────────────────

class TestCanonicalArgsDigest(unittest.TestCase):

    def test_same_args_same_digest(self):
        d1 = canonical_args_digest({"a": 1, "b": 2})
        d2 = canonical_args_digest({"b": 2, "a": 1})
        self.assertEqual(d1, d2)

    def test_different_values_different_digest(self):
        d1 = canonical_args_digest({"path": "/tmp/a.txt"})
        d2 = canonical_args_digest({"path": "/tmp/b.txt"})
        self.assertNotEqual(d1, d2)

    def test_empty_dict_produces_valid_hex(self):
        d = canonical_args_digest({})
        self.assertRegex(d, r"^[0-9a-f]{64}$")

    def test_none_treated_as_empty(self):
        self.assertEqual(canonical_args_digest(None), canonical_args_digest({}))

    def test_non_ascii_stable(self):
        d1 = canonical_args_digest({"content": "привет мир"})
        d2 = canonical_args_digest({"content": "привет мир"})
        self.assertEqual(d1, d2)

    def test_nested_structure(self):
        d1 = canonical_args_digest({"a": {"z": 1, "y": 2}})
        d2 = canonical_args_digest({"a": {"y": 2, "z": 1}})
        self.assertEqual(d1, d2)


# ── migration ─────────────────────────────────────────────────────────────────

class TestMigrateApprovalArgsSha256(unittest.TestCase):

    def test_adds_column_to_fresh_db(self):
        db = _make_db()
        try:
            _setup(db)
            with get_connection(db) as con:
                cols = {r[1] for r in con.execute("PRAGMA table_info(approvals)").fetchall()}
            self.assertIn("args_sha256", cols)
        finally:
            _safe_unlink(db)

    def test_adds_column_to_legacy_db_without_it(self):
        """Simulate a DB created before args_sha256 existed."""
        db = _make_db()
        try:
            # Create approvals table WITHOUT args_sha256
            with get_connection(db) as con:
                con.executescript("""
                    CREATE TABLE IF NOT EXISTS approvals (
                        id TEXT PRIMARY KEY,
                        tool_name TEXT NOT NULL,
                        agent_id TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT '',
                        run_id TEXT NOT NULL DEFAULT '',
                        project_scope_id TEXT NOT NULL DEFAULT '',
                        args_json TEXT NOT NULL DEFAULT '{}',
                        status TEXT NOT NULL DEFAULT 'pending',
                        ttl_seconds INTEGER NOT NULL DEFAULT 300,
                        expires_at TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL DEFAULT ''
                    )
                """)
            migrate_approval_args_sha256(db)
            with get_connection(db) as con:
                cols = {r[1] for r in con.execute("PRAGMA table_info(approvals)").fetchall()}
            self.assertIn("args_sha256", cols)
        finally:
            _safe_unlink(db)

    def test_migration_idempotent(self):
        db = _make_db()
        try:
            _setup(db)
            migrate_approval_args_sha256(db)  # second run — must not raise
        finally:
            _safe_unlink(db)

    def test_new_approval_stores_non_empty_digest(self):
        db = _make_db()
        try:
            _setup(db)
            args = {"path": "/tmp/x.txt", "content": "hello"}
            _make_approval(db, args=args)
            with get_connection(db) as con:
                row = con.execute("SELECT args_sha256 FROM approvals WHERE id='ap1'").fetchone()
            self.assertEqual(row["args_sha256"], canonical_args_digest(args))
            self.assertNotEqual(row["args_sha256"], "")
        finally:
            _safe_unlink(db)


# ── find_approved_approval binding ───────────────────────────────────────────

class TestFindApprovedApprovalBinding(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        _setup(self.db)
        _make_approval(self.db)
        update_approval_status(self.db, "ap1", status="approved")

    def tearDown(self):
        _safe_unlink(self.db)

    def test_exact_match_found(self):
        result = _find(self.db)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "ap1")

    def test_different_args_not_found(self):
        result = _find(self.db, args={"path": "/tmp/y.txt", "content": "other"})
        self.assertIsNone(result)

    def test_different_project_scope_id_not_found(self):
        result = _find(self.db, project_scope_id="scope-different")
        self.assertIsNone(result)

    def test_different_source_not_found(self):
        result = _find(self.db, source="chat")
        self.assertIsNone(result)

    def test_different_run_id_not_found(self):
        result = _find(self.db, run_id="run-999")
        self.assertIsNone(result)

    def test_empty_run_id_not_found(self):
        result = _find(self.db, run_id="")
        self.assertIsNone(result)

    def test_legacy_empty_digest_not_accepted(self):
        """An approval with args_sha256='' must never match, even if all other fields match."""
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=300)).isoformat()
        now_str = now.isoformat()
        with get_connection(self.db) as con:
            con.execute(
                """INSERT INTO approvals
                   (id, tool_name, agent_id, source, run_id, project_scope_id,
                    args_json, args_sha256, status, ttl_seconds, expires_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '', 'approved', 300, ?, ?, ?)""",
                (
                    "legacy-id",
                    "write_file", "code-agent", "code_agent", "run-123", "scope-abc",
                    '{"path": "/tmp/x.txt", "content": "hello"}',
                    expires_at, now_str, now_str,
                ),
            )
        # ap1 (has digest) should still be found; legacy-id must not
        result = _find(self.db)
        self.assertIsNotNone(result)
        self.assertNotEqual(result["id"], "legacy-id")

    def test_args_key_order_irrelevant(self):
        """Approval stored with one key order must match when args supplied in different order."""
        db2 = _make_db()
        try:
            _setup(db2)
            _make_approval(db2, id="ap2", args={"b": 2, "a": 1})
            update_approval_status(db2, "ap2", status="approved")
            # look up with reversed key order
            result = find_approved_approval(
                db2,
                tool_name="write_file",
                agent_id="code-agent",
                source="code_agent",
                run_id="run-123",
                project_scope_id="scope-abc",
                args={"a": 1, "b": 2},
            )
            self.assertIsNotNone(result)
        finally:
            _safe_unlink(db2)


# ── executor run_id validation ────────────────────────────────────────────────

class TestExecutorRunIdValidation(unittest.TestCase):

    def _run_exec(self, run_id: str) -> object:
        from app.application.agent_kernel.executor import ToolExecutionRequest, execute_tool
        from app.application.monitoring import runtime as _mon

        spec = {"permission": "require_approval", "max_output_chars": 50000,
                "policy_classified": True, "enabled": True}
        with mock.patch("app.application.tool_registry.runtime.get_tool", return_value=spec), \
             mock.patch("app.application.agent_registry.sandbox.preflight_or_raise"):
            return execute_tool(
                ToolExecutionRequest(
                    run_id=run_id,
                    agent_id="code-agent",
                    project_scope_id="scope-abc",
                    tool_name="write_file",
                    args={"path": "/tmp/x.txt"},
                    source="code_agent",
                ),
                dispatch_fn=lambda n, a: {"ok": True},
            )

    def test_empty_run_id_blocked(self):
        result = self._run_exec(run_id="")
        self.assertEqual(result.status, "blocked")
        self.assertIn("approval_requires_run_id", result.error or "")

    def test_empty_run_id_output_contains_reason(self):
        result = self._run_exec(run_id="")
        self.assertIn("approval_requires_run_id", result.output.get("error", ""))

    def test_non_empty_run_id_proceeds_past_run_id_check(self):
        from app.application.monitoring import runtime as _mon
        with mock.patch.object(_mon, "expire_old_approvals"), \
             mock.patch.object(_mon, "find_approved_approval", return_value=None), \
             mock.patch.object(_mon, "create_approval",
                               return_value={"id": "test-appr"}):
            result = self._run_exec(run_id="run-stable-123")
        # Should reach waiting_approval (not blocked by run_id check)
        self.assertEqual(result.status, "waiting_approval")


# ── API schema run_id ─────────────────────────────────────────────────────────

class TestAPIRunIdSchema(unittest.TestCase):

    def test_tool_execute_request_accepts_explicit_run_id(self):
        from app.schemas.tool_registry import ToolExecuteRequest
        req = ToolExecuteRequest(args={"a": 1}, run_id="my-run")
        self.assertEqual(req.run_id, "my-run")

    def test_tool_execute_request_run_id_optional(self):
        from app.schemas.tool_registry import ToolExecuteRequest
        req = ToolExecuteRequest(args={"a": 1})
        self.assertIsNone(req.run_id)

    def test_tool_execute_response_has_run_id_field(self):
        from app.schemas.tool_registry import ToolExecuteResponse
        resp = ToolExecuteResponse(ok=True, tool_name="write_file", run_id="abc123")
        self.assertEqual(resp.run_id, "abc123")

    def test_tool_execute_response_run_id_defaults_empty(self):
        from app.schemas.tool_registry import ToolExecuteResponse
        resp = ToolExecuteResponse(ok=True, tool_name="write_file")
        self.assertEqual(resp.run_id, "")


if __name__ == "__main__":
    unittest.main()
