"""Model-owned download intent and real file publication across public Resume."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.delivery_session import stream_resume_session
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.task_outcomes import TaskOutcome
from app.application.code_agent.tools._resources import tool_resource_publish
from app.core import config


def _reply(tool=None, arguments=None, text="Готово."):
    return {"message": {"content": "" if tool else text, "tool_calls":
        [{"function": {"name": tool, "arguments": arguments or {}}}] if tool else []}}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    data = tmp_path / "data"
    generated = data / "generated"
    generated.mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "GENERATED_DIR", generated)
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    return project


@pytest.mark.parametrize("published", [False, True])
def test_final_download_link_requires_observed_publication_without_declared_intent(workspace, published):
    (workspace / "result.csv").write_bytes(b"id,count\nA,5\n")
    # Merely finding a file in the download store is not a publication receipt.
    if not published:
        (config.GENERATED_DIR / "result.csv").write_bytes(b"id,count\nA,5\n")
    reply_text = "Обработка завершена. [Результат](/api/skills/download/result.csv)"
    replies = iter(([_reply("resource_publish", {"project_path": "result.csv"})] if published else [])
                   + [_reply(text=reply_text), _reply(text=reply_text)])
    events = list(stream_code_agent(
        user_message="Посчитай строки исходного файла.", project_root=workspace,
        run_id="final-link", base_tools=["resource_publish"],
        chat_fn=lambda **_kw: next(replies), auto_remember=False, permission_mode="bypass",
    ))
    final = next(event for event in events if event["type"] == "final_response")
    assert final["text"].startswith(reply_text if published else
                                    "Обработка завершена. Результат (публикация не подтверждена)")
    assert final["answer_status"] == ("complete" if published else "degraded")
    if not published:
        assert "Публикация для скачивания не подтверждена" in final["text"]
        assert "[Результат](/api/skills/download/result.csv)" not in final["text"]
    # Endpoint examples in a code task do not declare delivery.
    assert TaskOutcome().unbacked_download_links("Маршрут: `/api/skills/download/result.csv`") == []
    assert TaskOutcome().unbacked_download_links(
        "Пример: `[Файл](/api/skills/download/result.csv)`\n"
        "```markdown\n[Файл](/api/skills/download/result.csv)\n```\nГотово.") == []
    example = "Пример: `[Файл](/api/skills/download/result.csv)`\n"
    url = "/api/skills/download/result.csv"
    marked = TaskOutcome.mark_unbacked_download_links(example + f"[Результат]({url}) <{url}>", [url])
    assert marked == example + "Результат (публикация не подтверждена) Ссылка на файл (публикация не подтверждена)"


def test_gpu_transcript_publication_binds_only_actual_materialized_resource(workspace, monkeypatch):
    from app.application.code_agent.tools._resources import tool_resource_materialize
    from app.application.media import execution, resource_store
    from app.core import data_files

    monkeypatch.setattr(data_files, "DATA_DIR", config.DATA_DIR)
    original = resource_store.register_resource(
        original_name="voice.ogg", content_type="audio/ogg", owner_session="test", data=b"OggS")
    output = execution._local_gpu_transcript_result(original, "Полная запись, включая конец.")
    assert output["ok"] is True
    outcome = TaskOutcome()
    # The delivery target is a fact: a publication of transcript.txt was attempted (it did not exist yet).
    outcome.observe("resource_publish", {"project_path": "transcript.txt"}, {"ok": False}, project_root=workspace)
    outcome.observe("resource_process", {"operation": "transcribe"}, output, project_root=workspace)
    link = f"[Расшифровка]({output['download_url']})"
    assert outcome.unbacked_download_links(link) == []
    assert outcome.missing_deliveries() == [str(workspace / "transcript.txt")]
    derived = resource_store.get_record(output["resource"]["resource_id"])
    assert derived.storage_path not in json.dumps(outcome.snapshot())

    unrelated = resource_store.register_resource(
        original_name="same.txt", content_type="text/plain", owner_session="test",
        data=resource_store.read_bytes(derived))
    other = tool_resource_materialize(workspace, unrelated.resource_id, "other.txt")
    outcome.observe("resource_materialize", {}, other, project_root=workspace)
    assert outcome.missing_deliveries() == [str(workspace / "transcript.txt")]
    materialized = tool_resource_materialize(workspace, derived.resource_id, "transcript.txt")
    assert materialized["ok"] is True
    outcome.observe("resource_materialize", {}, materialized, project_root=workspace)
    restored = TaskOutcome(json.loads(json.dumps(outcome.snapshot())))
    assert restored.missing_deliveries() == []
    assert restored.unbacked_download_links(link) == []
    stored = config.GENERATED_DIR / output["download_name"]
    original_bytes = stored.read_bytes()
    stored.write_bytes(b"overwritten download")
    assert restored.unbacked_download_links(link) == [output["download_url"]]
    stored.write_bytes(original_bytes)
    assert TaskOutcome(restored.snapshot()).missing_deliveries() == [str(workspace / "transcript.txt")]
    assert TaskOutcome(restored.snapshot()).unbacked_download_links(link) == [output["download_url"]]
    stale_context = TaskOutcome(restored.snapshot()).context(0)
    assert output["download_url"] in stale_context
    assert all(item["status"] == "stale" for item in json.loads(stale_context.split("\n", 1)[1])["downloads"])
    assert derived.storage_path not in stale_context


