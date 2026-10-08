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


@pytest.mark.parametrize("retired_tool", ["resource_process", "file_gen"])
def test_retired_result_cannot_register_new_delivery(workspace, monkeypatch, retired_tool):
    from app.application.media import resource_store
    from app.core import data_files

    monkeypatch.setattr(data_files, "DATA_DIR", config.DATA_DIR)
    text = "[00:00:17] Полная запись, включая конец.\n"
    (workspace / "transcript.txt").write_text(text, encoding="utf-8", newline="\n")
    output = tool_resource_publish(workspace, "transcript.txt")
    assert output["ok"] is True
    record = resource_store.register_resource(
        original_name="transcript.txt", content_type="text/plain", owner_session="test", data=text.encode("utf-8"))
    legacy_output = {**output, "touched_path": "transcript.txt", "operation": "transcribe", "execution_target": "local_gpu",
                     "resource": resource_store.resource_ref(record)}
    outcome = TaskOutcome()
    outcome.observe(retired_tool, {"operation": "transcribe"}, legacy_output, project_root=workspace)
    assert outcome.deliveries == {}
    link = f"[Расшифровка]({output['download_url']})"
    assert outcome.unbacked_download_links(link) == [output["download_url"]]


@pytest.mark.parametrize("mirror", [False, True])
def test_legacy_file_gen_snapshot_preserves_only_real_target_binding(workspace, mirror):
    from openpyxl import Workbook
    from app.application.code_agent.task_outcomes import file_digest

    published = config.GENERATED_DIR / "report.xlsx"
    workbook = Workbook()
    workbook.active.append(["Число", 12345])
    workbook.save(published)
    workbook.close()
    target = workspace / "report.xlsx"
    target.write_bytes(published.read_bytes())
    url = "/api/skills/download/report.xlsx"
    receipt = {"target": str(target) if mirror else "", "tool": "file_gen",
               "status": "published", "download_name": published.name,
               "download_url": url, "sha256": file_digest(published)}
    saved = {"deliveries": {str(target) if mirror else url: receipt},
             "delivery_attempts": [str(target)]}
    restored = TaskOutcome(json.loads(json.dumps(saved)))
    link = f"[Отчёт]({url})"
    assert restored.unbacked_download_links(link) == []
    assert restored.missing_deliveries() == ([] if mirror else [str(target)])
    published.write_bytes(b"changed publication")
    stale = TaskOutcome(restored.snapshot())
    assert stale.unbacked_download_links(link) == [url]
    assert stale.missing_deliveries() == [str(target)]


def test_skill_transcript_uses_ordinary_publication_and_staleness(workspace):
    target = workspace / "transcript.txt"
    target.write_text("[00:00:17] Полная запись, включая конец.\n", encoding="utf-8", newline="\n")
    output = tool_resource_publish(workspace, "transcript.txt")
    assert output["ok"] is True
    outcome = TaskOutcome()
    outcome.observe("resource_publish", {"project_path": "transcript.txt"}, output, project_root=workspace)
    link = f"[Расшифровка]({output['download_url']})"
    assert outcome.missing_deliveries() == []
    assert outcome.unbacked_download_links(link) == []
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


@pytest.mark.parametrize("blob_change", ["overwrite", "delete"])
def test_legacy_transcript_snapshot_keeps_verified_download_and_materialization(workspace, monkeypatch, blob_change):
    from app.application.code_agent.tools._resources import tool_resource_materialize
    from app.application.media import resource_store
    from app.core import data_files

    monkeypatch.setattr(data_files, "DATA_DIR", config.DATA_DIR)
    text = "[00:00:17] Ранее опубликованная расшифровка.\n".encode("utf-8")
    derived = resource_store.register_resource(
        original_name="transcript.txt", content_type="text/plain", owner_session="test", data=text)
    stored = config.GENERATED_DIR / "transcript.txt"
    stored.write_bytes(text)
    url = "/api/skills/download/transcript.txt"
    target = str((workspace / "transcript.txt").resolve())
    # Restore a historical receipt without calling or rebuilding the retired runtime.
    outcome = TaskOutcome({"deliveries": {url: {
        "target": "", "tool": "resource_process", "status": "published",
        "resource_id": derived.resource_id, "sha256": derived.sha256,
        "download_name": "transcript.txt", "download_url": url,
    }}, "delivery_attempts": [target]})
    link = f"[Расшифровка]({url})"
    assert outcome.unbacked_download_links(link) == []
    assert outcome.missing_deliveries() == [target]

    unrelated = resource_store.register_resource(
        original_name="other.txt", content_type="text/plain", owner_session="test", data=text)
    other = tool_resource_materialize(workspace, unrelated.resource_id, "other.txt")
    assert other["ok"] is True
    outcome.observe("resource_materialize", {}, other, project_root=workspace)
    assert outcome.missing_deliveries() == [target]
    materialized = tool_resource_materialize(workspace, derived.resource_id, "transcript.txt")
    assert materialized["ok"] is True
    outcome.observe("resource_materialize", {}, materialized, project_root=workspace)
    restored = TaskOutcome(json.loads(json.dumps(outcome.snapshot())))
    assert restored.missing_deliveries() == []
    assert restored.unbacked_download_links(link) == []
    assert derived.storage_path not in json.dumps(restored.snapshot())

    blob = Path(derived.storage_path)
    if blob_change == "overwrite":
        blob.write_bytes(b"changed historical resource")
    else:
        blob.unlink()
    stale = TaskOutcome(restored.snapshot())
    assert stale.missing_deliveries() == [target]
    assert stale.unbacked_download_links(link) == [url]


