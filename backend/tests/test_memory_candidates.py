"""Tests — MemoryCandidate store + API (P3 Шаг 9).

Verifies:
1. CRUD: create, get, list, update_status (accept/reject), delete.
2. Only accepted candidates are returned by list_accepted_candidates.
3. API routes: list, get, accept (with optional content update), reject, delete.
4. Accepted candidates appear in _build_system_prompt.
"""
from __future__ import annotations

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
    mon_store.migrate_memory_candidates_table(db)
    return db


class TestMemoryCandidateCrud(unittest.TestCase):

    def setUp(self):
        self.db = _temp_db()

    def tearDown(self):
        try:
            self.db.unlink(missing_ok=True)
        except Exception:
            pass

    def test_create_and_get(self):
        c = mon_store.create_candidate(
            self.db,
            id="mc-1",
            namespace="project",
            content="Always use black for formatting.",
            source="agent",
            confidence=0.9,
            project_scope_id="scope:abc",
        )
        self.assertEqual(c["id"], "mc-1")
        self.assertEqual(c["status"], "pending")
        self.assertEqual(c["content"], "Always use black for formatting.")

        fetched = mon_store.get_candidate(self.db, "mc-1")
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["namespace"], "project")

    def test_list_by_status(self):
        for i in range(3):
            mon_store.create_candidate(self.db, id=f"mc-ls-{i}", content=f"fact {i}", project_scope_id="")
        items = mon_store.list_candidates(self.db, status="pending")
        self.assertEqual(len(items), 3)

    def test_accept_status(self):
        mon_store.create_candidate(self.db, id="mc-acc", content="Use pytest.", project_scope_id="")
        mon_store.update_candidate_status(self.db, "mc-acc", status="accepted")
        c = mon_store.get_candidate(self.db, "mc-acc")
        assert c is not None
        self.assertEqual(c["status"], "accepted")

    def test_accept_with_content_update(self):
        mon_store.create_candidate(self.db, id="mc-edit", content="Original.", project_scope_id="")
        mon_store.update_candidate_status(self.db, "mc-edit", status="accepted", content="Corrected.")
        c = mon_store.get_candidate(self.db, "mc-edit")
        assert c is not None
        self.assertEqual(c["content"], "Corrected.")

    def test_reject_status(self):
        mon_store.create_candidate(self.db, id="mc-rej", content="Wrong fact.", project_scope_id="")
        mon_store.update_candidate_status(self.db, "mc-rej", status="rejected")
        c = mon_store.get_candidate(self.db, "mc-rej")
        assert c is not None
        self.assertEqual(c["status"], "rejected")

    def test_delete(self):
        mon_store.create_candidate(self.db, id="mc-del", content="Delete me.", project_scope_id="")
        result = mon_store.delete_candidate(self.db, "mc-del")
        self.assertTrue(result["deleted"])
        self.assertIsNone(mon_store.get_candidate(self.db, "mc-del"))

    def test_list_accepted_only(self):
        mon_store.create_candidate(self.db, id="mc-a", content="A", project_scope_id="scope:x")
        mon_store.create_candidate(self.db, id="mc-b", content="B", project_scope_id="scope:x")
        mon_store.create_candidate(self.db, id="mc-c", content="C", project_scope_id="scope:x")
        mon_store.update_candidate_status(self.db, "mc-a", status="accepted")
        mon_store.update_candidate_status(self.db, "mc-c", status="rejected")

        accepted = mon_store.list_accepted_candidates(
            self.db, namespace="project", project_scope_id="scope:x"
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["id"], "mc-a")


class TestMemoryCandidateApi(unittest.TestCase):

    def _make_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.agent_monitor_routes import router
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def setUp(self):
        self.db = _temp_db()
        self._patcher = mock.patch.object(mon_runtime, "DB_PATH", self.db)
        self._patcher.start()
        self.client = self._make_client()

    def tearDown(self):
        self._patcher.stop()
        try:
            self.db.unlink(missing_ok=True)
        except Exception:
            pass

    def _create(self, id_="c1", content="test fact"):
        mon_store.create_candidate(self.db, id=id_, content=content, project_scope_id="")

    def test_list_empty(self):
        r = self.client.get("/api/agent-os/memory/candidates")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["total"], 0)

    def test_list_with_items(self):
        self._create("c1"); self._create("c2")
        r = self.client.get("/api/agent-os/memory/candidates")
        self.assertEqual(r.json()["total"], 2)

    def test_get_not_found(self):
        r = self.client.get("/api/agent-os/memory/candidates/nope")
        self.assertEqual(r.status_code, 404)

    def test_get_found(self):
        self._create("c3")
        r = self.client.get("/api/agent-os/memory/candidates/c3")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["id"], "c3")

    def test_accept(self):
        self._create("c4")
        r = self.client.post("/api/agent-os/memory/candidates/c4/accept")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "accepted")

    def test_accept_with_content(self):
        self._create("c5", content="Old content.")
        r = self.client.post(
            "/api/agent-os/memory/candidates/c5/accept?content=New+content."
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["content"], "New content.")

    def test_reject(self):
        self._create("c6")
        r = self.client.post("/api/agent-os/memory/candidates/c6/reject")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "rejected")

    def test_delete(self):
        self._create("c7")
        r = self.client.delete("/api/agent-os/memory/candidates/c7")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["deleted"])

    def test_cannot_accept_already_accepted(self):
        self._create("c8")
        mon_store.update_candidate_status(self.db, "c8", status="accepted")
        r = self.client.post("/api/agent-os/memory/candidates/c8/accept")
        self.assertEqual(r.status_code, 400)

    def test_filter_by_status(self):
        self._create("p1"); self._create("p2")
        mon_store.update_candidate_status(self.db, "p1", status="accepted")
        r = self.client.get("/api/agent-os/memory/candidates?status=accepted")
        self.assertEqual(r.json()["total"], 1)


class TestAcceptedCandidatesInPrompt(unittest.TestCase):
    """Accepted MemoryCandidates appear in the code-agent system prompt."""

    def test_accepted_candidate_injected_in_prompt(self):
        from app.application.code_agent.agent_loop import _build_system_prompt

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = _temp_db()
            mon_store.create_candidate(
                db, id="inj-1",
                content="Always write tests before code.",
                project_scope_id="scope:injtest",
                source="test",
            )
            mon_store.update_candidate_status(db, "inj-1", status="accepted")

            with mock.patch.object(mon_runtime, "DB_PATH", db), \
                 mock.patch("app.application.instructions.loader.Path.home",
                            return_value=Path(tmp) / "no_home"), \
                 mock.patch(
                     "app.application.projects.scope.project_scope_id",
                     return_value="scope:injtest",
                 ):
                prompt = _build_system_prompt(root)

        self.assertIn("Always write tests before code.", prompt)
        self.assertIn("Remembered facts", prompt)

    def test_pending_candidate_not_injected(self):
        from app.application.code_agent.agent_loop import _build_system_prompt

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = _temp_db()
            mon_store.create_candidate(
                db, id="pend-1",
                content="SHOULD_NOT_APPEAR",
                project_scope_id="scope:pendtest",
            )
            # status stays pending

            with mock.patch.object(mon_runtime, "DB_PATH", db), \
                 mock.patch("app.application.instructions.loader.Path.home",
                            return_value=Path(tmp) / "no_home"), \
                 mock.patch(
                     "app.application.projects.scope.project_scope_id",
                     return_value="scope:pendtest",
                 ):
                prompt = _build_system_prompt(root)

        self.assertNotIn("SHOULD_NOT_APPEAR", prompt)


if __name__ == "__main__":
    unittest.main()
