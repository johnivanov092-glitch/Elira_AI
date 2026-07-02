from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class UploadLimitTest(unittest.TestCase):
    """FIX-12: upload routes reject oversized bodies with HTTP 413."""

    def _client(self):
        from app.api.routes import files as files_route
        app = FastAPI()
        app.include_router(files_route.router)
        return files_route, TestClient(app)

    def test_oversized_upload_rejected_413(self):
        files_route, client = self._client()
        with patch.object(files_route, "MAX_UPLOAD_BYTES", 10):
            r = client.post(
                "/api/files/extract-text",
                files={"file": ("big.txt", b"x" * 64, "text/plain")},
            )
        self.assertEqual(r.status_code, 413)

    def test_small_upload_passes_size_gate(self):
        _files_route, client = self._client()
        r = client.post(
            "/api/files/extract-text",
            files={"file": ("a.txt", b"hello world", "text/plain")},
        )
        self.assertNotEqual(r.status_code, 413)  # under the limit → not rejected


class WalModeTest(unittest.TestCase):
    """FIX-9: hot DBs open in WAL so a writer doesn't block readers."""

    def test_hot_dbs_use_wal(self):
        from app.application.code_agent import sessions
        from app.application.smart_memory import store
        from app.application.task_planner import service
        from app.application.autopipeline import runtime as autopipeline
        from app.application.telegram import store as telegram
        conn_fns = [
            sessions._conn,
            store.connect_memory_db,
            service._connect,
            autopipeline._connect,
            telegram.connect_telegram_db,
        ]
        for conn_fn in conn_fns:
            conn = conn_fn()
            try:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(str(mode).lower(), "wal", f"{conn_fn.__module__} not WAL")
            finally:
                conn.close()


class ObservabilityRetentionTest(unittest.TestCase):
    """FIX-10: prune deletes rows older than the cutoff, keeps recent ones."""

    def test_prune_metrics_old_gone_recent_kept(self):
        from app.application.monitoring import store as mon
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "mon.db")
            mon.init_db(db)
            c = mon.get_connection(db)
            c.execute("INSERT INTO agent_metrics (metric_type, created_at) VALUES ('x', '2000-01-01T00:00:00+00:00')")
            c.execute("INSERT INTO agent_metrics (metric_type, created_at) VALUES ('x', '2999-01-01T00:00:00+00:00')")
            c.commit()
            c.close()
            res = mon.prune_old_metrics(db, cutoff_iso="2500-01-01T00:00:00+00:00")
            self.assertEqual(res["removed"]["agent_metrics"], 1)
            c = mon.get_connection(db)
            n = c.execute("SELECT COUNT(*) FROM agent_metrics").fetchone()[0]
            c.close()
            self.assertEqual(n, 1)

    def test_prune_events_old_gone_recent_kept(self):
        from app.application.event_bus import store as eb
        from app.application.event_bus.runtime import _CREATE_SQL
        from app.infrastructure.db.connection import connect_sqlite
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "eb.db")

            def factory():
                return connect_sqlite(db)

            eb.init_db(conn_factory=factory, create_sql=_CREATE_SQL)
            c = factory()
            c.execute("INSERT INTO events (event_id, event_type, created_at) VALUES ('e1', 't', '2000-01-01T00:00:00+00:00')")
            c.execute("INSERT INTO events (event_id, event_type, created_at) VALUES ('e2', 't', '2999-01-01T00:00:00+00:00')")
            c.commit()
            c.close()
            res = eb.prune_old_events(conn_factory=factory, cutoff_iso="2500-01-01T00:00:00+00:00")
            self.assertEqual(res["removed"]["events"], 1)
            c = factory()
            n = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            c.close()
            self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
