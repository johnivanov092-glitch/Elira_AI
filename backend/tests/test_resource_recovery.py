"""Missing-resource recovery uses the chat's real inputs, without a global listing."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.runtime_activation import RuntimeActivation
from app.application.code_agent.tools._resources import tool_resource_materialize
from app.application.code_agent.tools._shell import reset_current_run_id, set_current_run_id
from app.application.media import resource_store
from app.application.tool_providers.builtin import BuiltinToolProvider


@pytest.mark.parametrize("tool_name,args", [
    ("resource_process", {"resource_id": "voice_1.wav", "operation": "inspect"}),
    ("resource_materialize", {"resource_id": "https://example.com"}),
])
def test_missing_resource_lists_real_chat_attachment_and_task_file(tmp_path, monkeypatch, tool_name, args):
    (tmp_path / "voice_1.wav").write_bytes(b"local audio")
    attached = resource_store.register_resource(
        original_name="attached.txt", content_type="text/plain", owner_session="this-chat", data=b"attached")
    unrelated = resource_store.register_resource(
        original_name="other-chat.txt", content_type="text/plain", owner_session="other-chat", data=b"unrelated")
    monkeypatch.setattr(RunJournal, "load", Mock(return_value=SimpleNamespace(state={"request": {
        "resource_refs": [{"resource_id": attached.resource_id, "name": "invented metadata"}],
    }})))
    binding = set_current_run_id("chat-resource-hint")
    try:
        output = BuiltinToolProvider(tmp_path).dispatch(tool_name, args)
    finally:
        reset_current_run_id(binding)
        resource_store.discard(attached)
        resource_store.discard(unrelated)
    assert output["ok"] is False and output["error"] == "resource_not_found"
    assert "В этом чате нет вложения с таким id" in output["text"]
    assert "attached.txt" in output["text"] and attached.resource_id in output["text"]
    assert "Файлы в папке задачи: [\"voice_1.wav\"]" in output["text"]
    assert "other-chat" not in output["text"] and unrelated.resource_id not in output["text"]
    assert "invented metadata" not in output["text"] and attached.storage_path not in output["text"]
    assert "Для ссылок — web_fetch" in output["text"]


def test_missing_resource_without_attachments_lists_only_bounded_task_files(tmp_path):
    for number in range(40):
        (tmp_path / f"file-{number:02}.txt").write_text("text", encoding="utf-8")
    output = tool_resource_materialize(tmp_path, "invented-id")
    assert "Вложения чата: нет" in output["text"]
    assert "file-29.txt" in output["text"] and "file-30.txt" not in output["text"]
    assert "остальные файлы найди через glob" in output["text"]
    assert "invented-id" not in output["text"]


def test_missing_resource_ignores_deleted_chat_attachments(tmp_path, monkeypatch):
    monkeypatch.setattr(RunJournal, "load", Mock(return_value=SimpleNamespace(state={"request": {
        "resource_refs": [{"resource_id": "a" * 32}],
    }})))
    binding = set_current_run_id("missing-chat-attachment")
    try:
        result = tool_resource_materialize(tmp_path, "missing-id")
    finally:
        reset_current_run_id(binding)
    assert "Вложения чата: нет" in result["text"]


def test_loading_visible_capability_reports_noop_and_does_not_rebuild(tmp_path):
    provider = BuiltinToolProvider(tmp_path, tool_names={"capability_load", "memory", "library"})
    result = provider.dispatch("capability_load", {"group": "memory"})
    assert result["ok"] is True and result["already_available"] is True
    assert "уже доступны" in result["text"]
    rebuild = Mock(side_effect=AssertionError("visible group rebuilt registry"))
    activation = RuntimeActivation(tmp_path, registry_builder=rebuild)
    assert activation.apply_tool_result(
        "capability_load", {"group": "memory"}, result, status="ok", user_message="task",
    ) is None
    rebuild.assert_not_called()


def test_partially_visible_capability_still_loads(tmp_path):
    provider = BuiltinToolProvider(tmp_path, tool_names={"capability_load", "memory"})
    result = provider.dispatch("capability_load", {"group": "memory"})
    assert result["ok"] is True and "already_available" not in result
    assert result["activated_tools"] == ["library", "memory"]
