from __future__ import annotations

import sqlite3
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import code_agent_routes
from app.application.code_agent.agent_loop import stream_code_agent
from app.application.persona import store as persona_store
from app.application.persona import service as persona_service
from app.application.persona.service import (
    build_persona_prompt,
    list_persona_candidates,
    observe_dialogue,
)
from app.core.persona_defaults import ELIRA_PERSONA_BASE_PAYLOAD
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
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        code_agent_routes.session_store,
        "get_session",
        lambda session_id: {"id": session_id} if session_id == "workspace-chat-1" else None,
    )

    def fake_delivery_session(**_kwargs):
        yield {"type": "final_response", "text": "Давай, следующий шаг."}
        yield {"type": "done", "ok": True, "stop_reason": "answer", "steps": 1}

    monkeypatch.setattr(code_agent_routes, "stream_delivery_session", fake_delivery_session)
    monkeypatch.setattr(
        code_agent_routes,
        "_stream_with_workflow_requests",
        lambda events, **_kwargs: events,
    )
    app = FastAPI()
    app.include_router(code_agent_routes.router)

    with TestClient(app) as client:
        response = client.post(
            "/api/code-agent/stream",
            json={
                "message": "Подскажи, что делать дальше",
                "project_root": "",
                "run_id": "persona-main-agent-run",
                "session_id": "workspace-chat-1",
                "model": "test-model",
                "profile_name": "Баланс",
                "auto_remember": False,
            },
        )

    assert response.status_code == 200
    assert '"stop_reason": "answer"' in response.text
    assert any(
        item["trait_key"] == "supportive_guidance"
        for item in list_persona_candidates(limit=20)
    )


def test_untrusted_session_does_not_train_persona(
    isolated_persona_store,
    monkeypatch,
) -> None:
    monkeypatch.setattr(code_agent_routes.session_store, "get_session", lambda _session_id: None)

    def fake_delivery_session(**_kwargs):
        yield {"type": "final_response", "text": "Давай, следующий шаг."}
        yield {"type": "done", "ok": True, "stop_reason": "answer", "steps": 1}

    monkeypatch.setattr(code_agent_routes, "stream_delivery_session", fake_delivery_session)
    monkeypatch.setattr(
        code_agent_routes,
        "_stream_with_workflow_requests",
        lambda events, **_kwargs: events,
    )
    app = FastAPI()
    app.include_router(code_agent_routes.router)

    with TestClient(app) as client:
        response = client.post(
            "/api/code-agent/stream",
            json={
                "message": "Подскажи, что делать дальше",
                "project_root": "",
                "session_id": "draft-client-id",
                "model": "test-model",
            },
        )

    assert response.status_code == 200
    assert list_persona_candidates(limit=20) == []


def test_persona_storage_failure_does_not_break_completed_stream(
    isolated_persona_store,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        code_agent_routes.session_store,
        "get_session",
        lambda session_id: {"id": session_id},
    )

    def fail_observation(**_kwargs):
        raise sqlite3.OperationalError("persona store unavailable")

    monkeypatch.setattr(persona_service, "observe_dialogue", fail_observation)

    def fake_delivery_session(**_kwargs):
        yield {"type": "final_response", "text": "Готово."}
        yield {"type": "done", "ok": True, "stop_reason": "answer", "steps": 1}

    monkeypatch.setattr(code_agent_routes, "stream_delivery_session", fake_delivery_session)
    monkeypatch.setattr(
        code_agent_routes,
        "_stream_with_workflow_requests",
        lambda events, **_kwargs: events,
    )
    app = FastAPI()
    app.include_router(code_agent_routes.router)

    with TestClient(app) as client:
        response = client.post(
            "/api/code-agent/stream",
            json={
                "message": "Проверь",
                "project_root": "",
                "session_id": "workspace-chat-1",
            },
        )

    assert response.status_code == 200
    assert '"stop_reason": "answer"' in response.text


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

    prompt = persona_service.build_persona_context("test-model")

    assert "Структурированный и ясный ответ." in prompt
    assert len(prompt) <= 1300


def test_persona_prompt_bounds_long_promotions_across_layers(
    isolated_persona_store,
    monkeypatch,
) -> None:
    payload = deepcopy(ELIRA_PERSONA_BASE_PAYLOAD)
    markers = {
        "behavior_rules": "behavior-marker",
        "preferences": "preference-marker",
        "voice": "voice-marker",
        "values": "values-marker",
        "tool_style": "tool-style-marker",
    }
    for layer, marker in markers.items():
        payload[layer] = [*payload.get(layer, []), marker + "-" + ("я" * 4000)]
    monkeypatch.setattr(
        persona_service,
        "get_persona_version",
        lambda: {"version": 1, "payload": payload},
    )

    prompt = build_persona_prompt(
        "Баланс",
        "test-model",
        task_context="контекст-" + ("д" * 5000),
    )

    assert len(prompt) <= persona_service.PERSONA_PROMPT_CHAR_BUDGET
    assert "Идентичность: ты Elira" in prompt
    context = persona_service.build_persona_context("test-model")
    assert len(context) <= 700
    assert "[tone:" in context
    for marker in markers.values():
        assert marker in context
        assert marker not in prompt


def test_internal_agent_call_does_not_train_persona(
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
            user_message="Внутренний workflow шаг",
            project_root=tmp_path,
            run_id="internal-persona-run",
            session_id="synthetic-workflow-session",
            model="test-model",
            profile_name="Баланс",
            chat_fn=fake_chat,
            auto_remember=False,
            permission_mode="ask",
        )
    )

    assert events[-1]["stop_reason"] == "answer"
    assert list_persona_candidates(limit=20) == []
