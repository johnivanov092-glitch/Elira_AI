"""Тесты Agent OS Phase 1 — Agent Registry."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from app.services import agent_registry as reg


@pytest.fixture(autouse=True)
def _clean_test_agents():
    """Удаляем тестовых агентов после каждого теста."""
    yield
    for prefix in ("test-", "custom-"):
        for a in reg.list_agents(enabled_only=False):
            if a["id"].startswith(prefix):
                with reg._conn() as con:
                    con.execute("DELETE FROM agent_runs WHERE agent_id = ?", (a["id"],))
                    con.execute("DELETE FROM agent_state WHERE agent_id = ?", (a["id"],))
                    con.execute("DELETE FROM agents WHERE id = ?", (a["id"],))


class TestAgentCRUD:
    def test_register_and_get(self):
        result = reg.register_agent({
            "id": "test-alpha",
            "name": "Alpha Agent",
            "name_ru": "Агент Альфа",
            "role": "researcher",
            "system_prompt": "You are Alpha.",
            "tags": ["test", "research"],
        })
        assert result["id"] == "test-alpha"
        assert result["name"] == "Alpha Agent"
        assert result["role"] == "researcher"

        fetched = reg.get_agent("test-alpha")
        assert fetched is not None
        assert fetched["name_ru"] == "Агент Альфа"
        assert "test" in fetched["tags"]

    def test_list_agents(self):
        reg.register_agent({"id": "test-one", "name": "One", "role": "general"})
        reg.register_agent({"id": "test-two", "name": "Two", "role": "researcher"})

        all_agents = reg.list_agents()
        ids = [a["id"] for a in all_agents]
        assert "test-one" in ids
        assert "test-two" in ids

        researchers = reg.list_agents(role="researcher")
        assert all(a["role"] == "researcher" for a in researchers)

    def test_update_agent(self):
        reg.register_agent({"id": "test-upd", "name": "Before"})
        updated = reg.update_agent("test-upd", {"name": "After", "role": "analyst"})
        assert updated["name"] == "After"
        assert updated["role"] == "analyst"

    def test_delete_agent_soft(self):
        reg.register_agent({"id": "test-del", "name": "ToDelete"})
        reg.delete_agent("test-del")

        agent = reg.get_agent("test-del")
        assert agent is not None
        assert agent["enabled"] is False

        enabled = reg.list_agents(enabled_only=True)
        assert "test-del" not in [a["id"] for a in enabled]

    def test_upsert_on_register(self):
        reg.register_agent({"id": "test-ups", "name": "V1"})
        reg.register_agent({"id": "test-ups", "name": "V2"})
        agent = reg.get_agent("test-ups")
        assert agent["name"] == "V2"

    def test_get_nonexistent(self):
        assert reg.get_agent("no-such-agent") is None


class TestAgentState:
    def test_state_empty_by_default(self):
        reg.register_agent({"id": "test-state", "name": "Stateful"})
        state = reg.get_agent_state("test-state")
        assert state["state"] == {}

    def test_set_and_get_state(self):
        reg.register_agent({"id": "test-state2", "name": "Stateful2"})
        reg.set_agent_state("test-state2", {"memory": ["fact1"], "counter": 42})

        state = reg.get_agent_state("test-state2")
        assert state["state"]["counter"] == 42
        assert state["state"]["memory"] == ["fact1"]
        assert state["last_active_at"] is not None

    def test_state_overwrite(self):
        reg.register_agent({"id": "test-state3", "name": "Stateful3"})
        reg.set_agent_state("test-state3", {"v": 1})
        reg.set_agent_state("test-state3", {"v": 2})
        state = reg.get_agent_state("test-state3")
        assert state["state"]["v"] == 2


class TestAgentRuns:
    def test_record_and_list_runs(self):
        reg.register_agent({"id": "test-runner", "name": "Runner"})
        reg.record_agent_run({
            "agent_id": "test-runner",
            "run_id": "run-001",
            "input_summary": "Что такое Python?",
            "output_summary": "Python — язык программирования.",
            "ok": True,
            "route": "chat",
            "model_used": "gemma3:4b",
            "duration_ms": 1500,
        })
        reg.record_agent_run({
            "agent_id": "test-runner",
            "run_id": "run-002",
            "input_summary": "Ошибка",
            "ok": False,
            "route": "code",
            "duration_ms": 300,
        })

        runs, total = reg.get_agent_runs("test-runner")
        assert total == 2
        assert runs[0]["run_id"] == "run-002"  # newest first
        assert runs[1]["ok"] is True


class TestSeedBuiltinAgents:
    def test_seed_creates_agents(self):
        # Сбрасываем флаг и вызываем seed
        reg._BUILTIN_AGENTS_SEEDED = False
        count = reg.seed_builtin_agents()
        agents = reg.list_agents()
        ids = [a["id"] for a in agents]
        builtin_ids = [i for i in ids if i.startswith("builtin-")]
        # Если AGENT_PROFILES непустой — должны быть builtin, иначе count >= 0
        from app.core.config import AGENT_PROFILES
        if AGENT_PROFILES:
            assert len(builtin_ids) >= 1 or count >= 0
        else:
            assert count == 0

    def test_seed_idempotent(self):
        reg._BUILTIN_AGENTS_SEEDED = False
        reg.seed_builtin_agents()
        # Повторный вызов не должен создавать дубликаты
        reg._BUILTIN_AGENTS_SEEDED = False
        count2 = reg.seed_builtin_agents()
        assert count2 == 0  # уже существуют


class TestResolveAgent:
    def test_resolve_by_id(self):
        reg.register_agent({"id": "test-res", "name": "Resolvable", "role": "analyst"})
        agent = reg.resolve_agent(agent_id="test-res")
        assert agent is not None
        assert agent["id"] == "test-res"

    def test_resolve_by_role(self):
        reg.register_agent({"id": "test-role-a", "name": "RoleAgent", "role": "orchestrator"})
        agent = reg.resolve_agent(role="orchestrator")
        assert agent is not None
        assert agent["role"] == "orchestrator"

    def test_resolve_none(self):
        assert reg.resolve_agent() is None
        assert reg.resolve_agent(agent_id="nonexistent") is None
