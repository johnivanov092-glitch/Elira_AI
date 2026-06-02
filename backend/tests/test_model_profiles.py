"""Tests — Model Profile Registry (P8 Шаг 16)."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.monitoring import store as mon_store  # noqa: E402
from app.application.monitoring import runtime as mon_runtime  # noqa: E402


def _temp_db() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = Path(tmp.name)
    mon_store.migrate_model_profiles_table(db)
    return db


def _connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    return con


class TestModelProfilesMigration(unittest.TestCase):

    def test_migration_creates_table(self):
        db = _temp_db()
        try:
            con = _connect(db)
            cols = {r[1] for r in con.execute("PRAGMA table_info(model_profiles)").fetchall()}
            con.close()
            for col in ("id", "provider", "model", "role", "context_limit",
                        "timeout_seconds", "enabled", "cloud_consent_required"):
                self.assertIn(col, cols)
        finally:
            db.unlink(missing_ok=True)

    def test_migration_seeds_defaults(self):
        db = _temp_db()
        try:
            profiles = mon_store.list_model_profiles(db)
            self.assertGreaterEqual(len(profiles), 4)
            roles = {p["role"] for p in profiles}
            self.assertIn("fast", roles)
            self.assertIn("code", roles)
            self.assertIn("strong", roles)
            self.assertIn("embedding", roles)
        finally:
            db.unlink(missing_ok=True)

    def test_migration_idempotent(self):
        db = _temp_db()
        try:
            mon_store.migrate_model_profiles_table(db)  # second run
            profiles = mon_store.list_model_profiles(db)
            ids = [p["id"] for p in profiles]
            self.assertEqual(len(ids), len(set(ids)), "Duplicate profiles after idempotent migration")
        finally:
            db.unlink(missing_ok=True)


class TestModelProfilesCrud(unittest.TestCase):

    def setUp(self):
        self.db = _temp_db()

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_list_all_profiles(self):
        profiles = mon_store.list_model_profiles(self.db)
        self.assertGreater(len(profiles), 0)
        for p in profiles:
            self.assertIn("enabled", p)
            self.assertIsInstance(p["enabled"], bool)
            self.assertIsInstance(p["cloud_consent_required"], bool)

    def test_cloud_profiles_disabled_by_default(self):
        profiles = mon_store.list_model_profiles(self.db)
        cloud = [p for p in profiles if p["cloud_consent_required"]]
        for p in cloud:
            self.assertFalse(p["enabled"],
                             f"{p['id']} is a cloud profile and should be disabled by default")

    def test_local_fast_enabled_by_default(self):
        p = mon_store.get_model_profile(self.db, "local-fast")
        self.assertIsNotNone(p)
        self.assertTrue(p["enabled"])
        self.assertFalse(p["cloud_consent_required"])

    def test_get_profile(self):
        p = mon_store.get_model_profile(self.db, "local-code")
        self.assertIsNotNone(p)
        self.assertEqual(p["role"], "code")

    def test_get_unknown_profile_returns_none(self):
        self.assertIsNone(mon_store.get_model_profile(self.db, "nonexistent"))

    def test_enable_profile(self):
        # local-strong is disabled by default
        mon_store.set_model_profile_enabled(self.db, "local-strong", enabled=True)
        p = mon_store.get_model_profile(self.db, "local-strong")
        self.assertTrue(p["enabled"])

    def test_disable_profile(self):
        mon_store.set_model_profile_enabled(self.db, "local-code", enabled=False)
        p = mon_store.get_model_profile(self.db, "local-code")
        self.assertFalse(p["enabled"])

    def test_get_profile_for_role(self):
        p = mon_store.get_profile_for_role(self.db, "fast")
        self.assertIsNotNone(p)
        self.assertEqual(p["role"], "fast")
        self.assertTrue(p["enabled"])

    def test_get_profile_for_disabled_role_returns_none(self):
        # Disable all strong profiles
        profiles = mon_store.list_model_profiles(self.db, role="strong")
        for p in profiles:
            mon_store.set_model_profile_enabled(self.db, p["id"], enabled=False)
        result = mon_store.get_profile_for_role(self.db, "strong")
        self.assertIsNone(result)

    def test_list_by_role(self):
        profiles = mon_store.list_model_profiles(self.db, role="code")
        for p in profiles:
            self.assertEqual(p["role"], "code")

    def test_list_enabled_only(self):
        profiles = mon_store.list_model_profiles(self.db, enabled_only=True)
        for p in profiles:
            self.assertTrue(p["enabled"])


class TestModelProfilesApi(unittest.TestCase):

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.agent_monitor_routes import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.db = _temp_db()
        self._patcher = mock.patch.object(mon_runtime, "DB_PATH", self.db)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.db.unlink(missing_ok=True)

    def test_list_profiles(self):
        r = self.client.get("/api/agent-os/models/profiles")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("profiles", data)
        self.assertGreater(len(data["profiles"]), 0)

    def test_get_profile(self):
        r = self.client.get("/api/agent-os/models/profiles/local-fast")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["role"], "fast")

    def test_get_unknown_profile(self):
        r = self.client.get("/api/agent-os/models/profiles/xyz")
        self.assertEqual(r.status_code, 404)

    def test_enable_profile(self):
        r = self.client.post("/api/agent-os/models/profiles/local-strong/enable")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["enabled"])

    def test_disable_profile(self):
        r = self.client.post("/api/agent-os/models/profiles/local-code/disable")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["enabled"])

    def test_cloud_profile_disabled_by_default(self):
        r = self.client.get("/api/agent-os/models/profiles/cloud-claude-sonnet")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["enabled"])
        self.assertTrue(r.json()["cloud_consent_required"])

    def test_role_lookup(self):
        r = self.client.get("/api/agent-os/models/role/fast")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["role"], "fast")

    def test_role_no_enabled_returns_404(self):
        # Disable all embedding profiles first
        mon_store.set_model_profile_enabled(self.db, "local-embedding", enabled=False)
        r = self.client.get("/api/agent-os/models/role/embedding")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
