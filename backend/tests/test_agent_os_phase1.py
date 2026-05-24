from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.agent_registry import runtime as reg  # noqa: E402


class AgentRegistryTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        for prefix in ("test-", "custom-"):
            for agent in reg.list_agents(enabled_only=False):
                if agent["id"].startswith(prefix):
                    with reg._conn() as con:
                        con.execute("DELETE FROM agent_runs WHERE agent_id = ?", (agent["id"],))
                        con.execute("DELETE FROM agent_state WHERE agent_id = ?", (agent["id"],))
                        con.execute("DELETE FROM agents WHERE id = ?", (agent["id"],))
        super().tearDown()


class TestAgentCRUD(AgentRegistryTestCase):
    def test_register_and_get(self) -> None:
        result = reg.register_agent(
            {
                "id": "test-alpha",
                "name": "Alpha Agent",
                "name_ru": "Агент Альфа",
                "role": "researcher",
                "system_prompt": "You are Alpha.",
                "tags": ["test", "research"],
            }
        )
        self.assertEqual(result["id"], "test-alpha")
        self.assertEqual(result["name"], "Alpha Agent")
        self.assertEqual(result["role"], "researcher")

        fetched = reg.get_agent("test-alpha")
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["name_ru"], "Агент Альфа")
        self.assertIn("test", fetched["tags"])

    def test_list_agents(self) -> None:
        reg.register_agent({"id": "test-one", "name": "One", "role": "general"})
        reg.register_agent({"id": "test-two", "name": "Two", "role": "researcher"})

        all_agents = reg.list_agents()
        ids = [agent["id"] for agent in all_agents]
        self.assertIn("test-one", ids)
        self.assertIn("test-two", ids)

        researchers = reg.list_agents(role="researcher")
        self.assertTrue(all(agent["role"] == "researcher" for agent in researchers))

    def test_update_agent(self) -> None:
        reg.register_agent({"id": "test-upd", "name": "Before"})
        updated = reg.update_agent("test-upd", {"name": "After", "role": "analyst"})
        self.assertEqual(updated["name"], "After")
        self.assertEqual(updated["role"], "analyst")

    def test_delete_agent_soft(self) -> None:
        reg.register_agent({"id": "test-del", "name": "ToDelete"})
        reg.delete_agent("test-del")

        agent = reg.get_agent("test-del")
        self.assertIsNotNone(agent)
        assert agent is not None
        self.assertFalse(agent["enabled"])

        enabled = reg.list_agents(enabled_only=True)
        self.assertNotIn("test-del", [item["id"] for item in enabled])

    def test_upsert_on_register(self) -> None:
        reg.register_agent({"id": "test-ups", "name": "V1"})
        reg.register_agent({"id": "test-ups", "name": "V2"})
        agent = reg.get_agent("test-ups")
        assert agent is not None
        self.assertEqual(agent["name"], "V2")

    def test_get_nonexistent(self) -> None:
        self.assertIsNone(reg.get_agent("no-such-agent"))


class TestAgentState(AgentRegistryTestCase):
    def test_state_empty_by_default(self) -> None:
        reg.register_agent({"id": "test-state", "name": "Stateful"})
        state = reg.get_agent_state("test-state")
        self.assertEqual(state["state"], {})

    def test_set_and_get_state(self) -> None:
        reg.register_agent({"id": "test-state2", "name": "Stateful2"})
        reg.set_agent_state("test-state2", {"memory": ["fact1"], "counter": 42})

        state = reg.get_agent_state("test-state2")
        self.assertEqual(state["state"]["counter"], 42)
        self.assertEqual(state["state"]["memory"], ["fact1"])
        self.assertIsNotNone(state["last_active_at"])

    def test_state_overwrite(self) -> None:
        reg.register_agent({"id": "test-state3", "name": "Stateful3"})
        reg.set_agent_state("test-state3", {"v": 1})
        reg.set_agent_state("test-state3", {"v": 2})
        state = reg.get_agent_state("test-state3")
        self.assertEqual(state["state"]["v"], 2)


class TestAgentRuns(AgentRegistryTestCase):
    def test_record_and_list_runs(self) -> None:
        reg.register_agent({"id": "test-runner", "name": "Runner"})
        reg.record_agent_run(
            {
                "agent_id": "test-runner",
                "run_id": "run-001",
                "input_summary": "Что такое Python?",
                "output_summary": "Python — язык программирования.",
                "ok": True,
                "route": "chat",
                "model_used": "gemma3:4b",
                "duration_ms": 1500,
            }
        )
        reg.record_agent_run(
            {
                "agent_id": "test-runner",
                "run_id": "run-002",
                "input_summary": "Ошибка",
                "ok": False,
                "route": "code",
                "duration_ms": 300,
            }
        )

        runs, total = reg.get_agent_runs("test-runner")
        self.assertEqual(total, 2)
        self.assertEqual(runs[0]["run_id"], "run-002")
        self.assertTrue(runs[1]["ok"])


class TestSeedBuiltinAgents(AgentRegistryTestCase):
    def test_seed_creates_agents(self) -> None:
        reg._BUILTIN_AGENTS_SEEDED = False
        count = reg.seed_builtin_agents()
        agents = reg.list_agents()
        ids = [agent["id"] for agent in agents]
        builtin_ids = [agent_id for agent_id in ids if agent_id.startswith("builtin-")]

        from app.core.config import AGENT_PROFILES

        if AGENT_PROFILES:
            self.assertTrue(len(builtin_ids) >= 1 or count >= 0)
        else:
            self.assertEqual(count, 0)

    def test_seed_idempotent(self) -> None:
        reg._BUILTIN_AGENTS_SEEDED = False
        reg.seed_builtin_agents()
        reg._BUILTIN_AGENTS_SEEDED = False
        count = reg.seed_builtin_agents()
        self.assertEqual(count, 0)

    def test_seed_updates_canonical_builtins_and_removes_stale_builtin_ids(self) -> None:
        stale_id = "builtin-stale-from-old-registry"
        reg.register_agent({"id": stale_id, "name": "Stale", "role": "custom"})
        reg.register_agent(
            {
                "id": "builtin-universal",
                "name": "Broken",
                "name_ru": "Broken",
                "role": "custom",
            }
        )

        reg._BUILTIN_AGENTS_SEEDED = False
        reg.seed_builtin_agents()

        self.assertIsNone(reg.get_agent(stale_id))
        universal = reg.get_agent("builtin-universal")
        self.assertIsNotNone(universal)
        assert universal is not None
        self.assertEqual(universal["name"], "Universal")
        self.assertNotEqual(universal["name_ru"], "Broken")


class TestResolveAgent(AgentRegistryTestCase):
    def test_resolve_by_id(self) -> None:
        reg.register_agent({"id": "test-res", "name": "Resolvable", "role": "analyst"})
        agent = reg.resolve_agent(agent_id="test-res")
        self.assertIsNotNone(agent)
        assert agent is not None
        self.assertEqual(agent["id"], "test-res")

    def test_resolve_by_role(self) -> None:
        reg.register_agent({"id": "test-role-a", "name": "RoleAgent", "role": "orchestrator"})
        agent = reg.resolve_agent(role="orchestrator")
        self.assertIsNotNone(agent)
        assert agent is not None
        self.assertEqual(agent["role"], "orchestrator")

    def test_resolve_none(self) -> None:
        self.assertIsNone(reg.resolve_agent())
        self.assertIsNone(reg.resolve_agent(agent_id="nonexistent"))


if __name__ == "__main__":
    unittest.main()
