from __future__ import annotations

import sqlite3

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.persona import store as persona_store
from app.application.persona.service import (
    build_persona_prompt,
    list_persona_candidates,
    observe_dialogue,
)
from app.infrastructure.db.connection import connect_sqlite


@pytest.fixture
def isolated_persona_store(tmp_path, monkeypatch):
    db_path = tmp_path / "persona-main-agent.db"

    def connect():
        return connect_sqlite(
            db_path,
            row_factory=sqlite3.Row,
            journal_mode=None,
        )

    monkeypatch.setattr(persona_store, "connect", connect)
    persona_store.bootstrap_if_needed()
    return db_path


def test_main_agent_dialogue_creates_persona_candidate(
    isolated_persona_store,
    tmp_path,
) -> None:
    def fake_chat(**_kwargs):
        return {
            "message": {
                "content": "Давай, следующий шаг.",
                "tool_calls": [],
            }
        }

    events = list(
        stream_code_agent(
            user_message="Подскажи, что делать дальше",
            project_root=tmp_path,
            run_id="persona-main-agent-run",
            session_id="workspace-chat-1",
            model="test-model",
            profile_name="Баланс",
            chat_fn=fake_chat,
            auto_remember=False,
            permission_mode="ask",
        )
    )

    assert events[-1]["type"] == "done"
    assert events[-1]["stop_reason"] == "answer"
    assert any(
        item["trait_key"] == "supportive_guidance"
        for item in list_persona_candidates(limit=20)
    )


def test_promoted_preference_reaches_persona_prompt(
    isolated_persona_store,
) -> None:
    sessions = ("workspace-chat-1", "workspace-chat-1", "workspace-chat-2")
    for index, session_id in enumerate(sessions):
        observe_dialogue(
            dialog_id=f"dialog-{index}",
            session_id=session_id,
            profile_name="Баланс",
            model_name="test-model",
            user_input="Покажи итог",
            answer_text="Итог: структурированный вывод.",
            outcome_ok=True,
        )

    prompt = build_persona_prompt("Баланс", "test-model")

    assert "Структурированный и ясный ответ." in prompt
    assert len(prompt) <= 1300
