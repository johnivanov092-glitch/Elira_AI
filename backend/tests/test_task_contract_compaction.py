"""Compaction keeps the full goal and every requirement out of the lossy summary."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.application.code_agent import loop_helpers
from app.application.code_agent.history import project_runtime_roles
from app.application.code_agent.turn_context import TurnContext
from app.application.context.compaction import (
    maybe_compact, TASK_CONTRACT_PREFIX, TASK_CONTRACT_MARKER_VALUE, TASK_STATE_MARKER_KEY,
)


def test_compaction_keeps_full_goal_and_all_requirements_out_of_lossy_summary(tmp_path):
    context = TurnContext(messages=[{"role": "system", "content": "System"}],
        raw_user_message="Исходная цель с неизменным CSV", root=tmp_path, working_dir=tmp_path, run_id="compact")
    rows = [{"requirement_id": str(i), "text": "Требование " + str(i), "status": "unconfirmed"} for i in range(14)]
    context.refresh_task_state = True
    context.update_task_state(task_spec=None, criteria_rows=rows, checklist_items=[], mutated_files=[],
                              verifications=[], failed_attempts=[])
    original = next(row for row in context.messages if row.get(TASK_STATE_MARKER_KEY) == TASK_CONTRACT_MARKER_VALUE)
    context.messages.extend({"role": "user", "content": "history " + str(i)} for i in range(12))
    summarized = []
    def summary(**kwargs):
        summarized.extend(kwargs["messages"])
        return {"ok": True, "summary": "lossy"}
    packed, compacted = maybe_compact(context.messages, num_ctx=100, model="fixture", chat_fn=None,
                                      summarize_fn=summary, threshold=0, keep_pairs=1)
    assert compacted and original in packed and original not in summarized
    assert "Требование 13" in original["content"] and "Исходная цель" in original["content"]


def update(context, task_spec=None):
    context.refresh_task_state = True
    context.update_task_state(task_spec=task_spec, criteria_rows=[{
        "requirement_id": "files", "text": "12 файлов и manifest.txt", "mandatory": True,
        "status": "confirmed",
    }], checklist_items=[], mutated_files=[], verifications=[], failed_attempts=[])


def contract(context):
    message = next(row for row in context.messages if row.get(TASK_STATE_MARKER_KEY) == TASK_CONTRACT_MARKER_VALUE)
    return json.loads(message["content"][len(TASK_CONTRACT_PREFIX):])


@pytest.mark.parametrize("resume", [False, True])
def test_full_request_occurs_once_after_compaction_and_resume(tmp_path, resume):
    request = "Прежние факты RUNE-M9T2 / Кедр / 43 / синий.\n" + "Archive row: meadow river cedar stone.\n" * 8000
    user = {"role": "user", "content": request}
    messages = [{"role": "system", "content": "System"}, user]
    context = TurnContext(messages=messages, raw_user_message=request, root=tmp_path,
                          working_dir=tmp_path, run_id="original")
    update(context, SimpleNamespace(goal=request, constraints=[]))
    assert sum(str(message.get("content") or "").count("Archive row: meadow river cedar stone.")
               for message in project_runtime_roles(context.messages)) < 8012
    if resume:
        messages = json.loads(json.dumps(context.messages, ensure_ascii=False))
        messages.append({"role": "user", "content": "Продолжи тот же прогон"})
        context = TurnContext(messages=messages, raw_user_message="Продолжи тот же прогон",
                              root=tmp_path, working_dir=tmp_path, run_id="original")
        context.original_goal = request
        update(context, SimpleNamespace(goal=request, constraints=[]))
    context.messages.extend({"role": "assistant", "content": "old work " + str(i)} for i in range(12))
    context.messages.append({"role": "user", "content": "Уточнение: сохрани manifest.txt"})
    context.clarifications = ["Уточнение: сохрани manifest.txt"]
    update(context, SimpleNamespace(goal=request, constraints=[]))
    packed, changed = maybe_compact(
        context.messages, num_ctx=131072, model="fixture", chat_fn=None,
        summarize_fn=lambda **kwargs: {"ok": True, "summary": "Earlier work"},
        threshold=0, keep_pairs=1, pinned_message_ids=context.task_input_message_ids,
    )
    assert changed
    provider = project_runtime_roles(packed)
    assert sum(str(message.get("content") or "").count("Archive row: meadow river cedar stone.")
               for message in provider) < 8012
    restored = next(message for message in packed if message.get("_msg_id") == contract(context)["original_goal"]["message_id"])
    assert restored["role"] == "user" and restored["content"] == request
    assert "12 файлов и manifest.txt" in provider[0]["content"]
    assert any(message.get("content") == "Уточнение: сохрани manifest.txt" for message in packed)


def test_unavailable_original_goal_kept_once_in_contract(tmp_path):
    context = TurnContext(messages=[{"role": "system", "content": "System"}], raw_user_message="Исходная цель",
                          root=tmp_path, working_dir=tmp_path, run_id="fallback")
    update(context)
    payload = contract(context)
    assert payload["original_goal"] == "Исходная цель"
    assert payload["goal"] == {"field": "original_goal"}


@pytest.mark.parametrize("wrapped", [False, True])
def test_repeated_request_does_not_pin_large_historical_archive(tmp_path, wrapped):
    request = "Проверь исходные данные. " * 10
    messages = [{"role": "system", "content": "System"},
                {"role": "user", "content": request + "old archive " * 100000}]
    if not wrapped:
        messages.append({"role": "user", "content": request})
    current = {"role": "user", "content": f"Context\n{request}" if wrapped else request}
    messages.append(current)
    context = TurnContext(messages=messages, raw_user_message=request, root=tmp_path,
                          working_dir=tmp_path, run_id="repeated")
    update(context)
    reference = contract(context)["original_goal"]
    assert reference["message_id"] == current["_msg_id"]
    assert context.task_input_message_ids == {current["_msg_id"]}


def test_distinct_goal_and_clarification_remain_protected(tmp_path):
    original, amended = "Проверь исходный файл", "Теперь исправь файл"
    messages = [{"role": "system", "content": "System"}, {"role": "user", "content": original},
                {"role": "user", "content": amended}]
    context = TurnContext(messages=messages, raw_user_message=original, root=tmp_path,
                          working_dir=tmp_path, run_id="amend")
    context.clarifications = [amended]
    update(context, SimpleNamespace(goal=amended, constraints=[]))
    payload = contract(context)
    packed, _ = maybe_compact(
        context.messages, num_ctx=100, model="fixture", chat_fn=None,
        summarize_fn=lambda **kwargs: {"ok": True, "summary": "lossy"}, threshold=0, keep_pairs=0,
    )
    context.messages = packed
    assert contract(context)["original_goal"] == original
    assert contract(context)["goal"] == amended
    assert contract(context)["clarifications"] == [amended]
    assert payload["requirements"][0]["mandatory"] is True


def test_prepare_preserves_referenced_input_after_later_clarification(tmp_path, monkeypatch):
    request = "Проверь Кедр и manifest.txt.\n" + "archive data\n" * 25000
    context = TurnContext(messages=[{"role": "system", "content": "System"},
        {"role": "user", "content": request}, {"role": "assistant", "content": "old work " * 50000},
        *[{"role": "assistant", "content": "Earlier result " + str(i)} for i in range(6)],
        {"role": "user", "content": "Не меняй исходный файл"}],
        raw_user_message=request, root=tmp_path, working_dir=tmp_path, run_id="prepare")
    context.guidance_message_ids = set()
    context.clarifications = ["Не меняй исходный файл"]
    update(context)
    monkeypatch.setattr(loop_helpers, "summarize_history", lambda **kwargs: {"ok": True, "summary": "Earlier work"})
    changed, usage = context.prepare(
        prepare_fn=loop_helpers._prepare_messages_for_llm, num_ctx=131072, model="fixture", chat_fn=None,
        context_profile={"reserved_output_tokens": 4096, "safe_input_budget": 120832},
        tool_schemas=[], cancel_handle=None,
        audit_sink=None, restore_source_context=lambda messages, **kwargs: messages,
    )
    assert changed and usage["current_tokens"] < 120832
    assert any(message.get("content") == request for message in context.messages)
    assert any(message.get("content") == "Не меняй исходный файл" for message in context.messages)
