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
from app.application.code_agent.task_outcomes import TaskOutcome, task_decide
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


@pytest.mark.parametrize("explicit_none", [False, True])
def test_download_button_code_goal_does_not_require_chat_publication(workspace, explicit_none):
    decision = {"disposition": "one_off", "reason": "Изменить функцию приложения",
                "targets": ["download.ts", "archive.py"], "requirements": [
                    {"id": "req-ui", "text": "Кнопка UI скачивает ZIP с backend", "mandatory": True},
                    {"id": "req-backend", "text": "Backend создаёт ZIP с результатом", "mandatory": True},
                ]}
    if explicit_none:
        decision["delivery"] = {"mode": "none", "targets": []}
    checker = workspace / "check_download.py"
    checker.write_text(
        "import json, runpy\nfrom io import BytesIO\nfrom pathlib import Path\nfrom zipfile import ZipFile\n"
        "source = Path('download.ts').read_text(encoding='utf-8')\n"
        "ui = all(part in source for part in [\"fetch('/api/archive.zip')\", 'response.blob()', 'link.download = archiveName', 'link.click()'])\n"
        "payload = runpy.run_path('archive.py')['archive_zip']()\n"
        "with ZipFile(BytesIO(payload)) as archive:\n"
        "    backend = archive.namelist() == ['report.txt'] and archive.read('report.txt') == b'report'\n"
        "Path('download_checks.json').write_text(json.dumps({'checks': [\n"
        "    {'name': 'UI download', 'requirement_id': 'req-ui', 'passed': ui},\n"
        "    {'name': 'backend ZIP', 'requirement_id': 'req-backend', 'passed': backend}]}), encoding='utf-8')\n",
        encoding="utf-8", newline="\n",
    )
    arguments = [sys.executable, str(checker)]
    command = subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
    replies = iter([
        _reply("runtime_control", {"operation": "task_decide", "config": decision}),
        _reply("write_file", {"path": "download.ts", "content":
            "export const archiveName = 'report.zip';\n"
            "export async function downloadArchive() {\n"
            "  const response = await fetch('/api/archive.zip');\n"
            "  const link = document.createElement('a');\n"
            "  link.href = URL.createObjectURL(await response.blob());\n"
            "  link.download = archiveName;\n  link.click();\n}\n"}),
        _reply("write_file", {"path": "archive.py", "content":
            "from io import BytesIO\nfrom zipfile import ZipFile\n"
            "def archive_zip():\n    buffer = BytesIO()\n"
            "    with ZipFile(buffer, 'w') as archive:\n        archive.writestr('report.txt', 'report')\n"
            "    return buffer.getvalue()\n"}),
        _reply("runtime_control", {"operation": "result_verify", "config": {
            "command": command, "targets": decision["targets"], "report_path": "download_checks.json",
        }}),
        _reply(text="Реализована кнопка скачивания архива в приложении."),
    ])
    events = list(stream_code_agent(
        user_message="Реализуй кнопку скачивания ZIP в UI и backend.", project_root=workspace,
        run_id="download-button", base_tools=["runtime_control", "write_file"],
        chat_fn=lambda **_kw: next(replies), auto_remember=False, permission_mode="bypass",
    ))
    assert (workspace / "download.ts").is_file()
    final = next(event for event in events if event["type"] == "final_response")
    assert final["text"] == "Реализована кнопка скачивания архива в приложении."
    assert final["answer_status"] == "complete"
    assert not final["task_outcome"]["delivery_attempts"]
    assert not any(event.get("tool") == "resource_publish" for event in events)
    state = RunJournal.load("download-button").state
    outcome = TaskOutcome(state["task_outcome"])
    epoch = int(state.get("code_input_epoch") or 0)
    assert outcome.checks_current([str(workspace / path) for path in decision["targets"]], epoch)
    assert outcome.missing_requirements(epoch) == []


def _decide(outcome, root, **extra):
    result = task_decide(root, {"disposition": "one_off", "reason": "Текущее решение задачи", **extra})
    outcome.observe("runtime_control", {"operation": "task_decide"}, {"ok": True, "result": result})


def test_delivery_update_preserves_contract_and_requires_exact_published_target(workspace):
    for name in ("requested.csv", "other.csv"):
        (workspace / name).write_bytes(b"same bytes are not the same target\n")
    outcome = TaskOutcome()
    _decide(outcome, workspace, delivery={"mode": "chat_download", "targets": ["requested.csv"]})
    _decide(outcome, workspace, targets=["corrected-result.csv"])
    wanted = str(workspace / "requested.csv")
    assert outcome.decision["delivery"]["targets"] == [wanted]

    # A real successful publication with identical bytes but a different path
    # cannot satisfy the declared target.
    other = tool_resource_publish(workspace, project_path="other.csv")
    outcome.observe("resource_publish", {"project_path": "other.csv"}, other, project_root=workspace)
    assert other["ok"] is True
    assert outcome.missing_deliveries() == [wanted]
    requested = tool_resource_publish(workspace, project_path="requested.csv")

    # file_gen must provide its actual mirror path; basename guessing is forbidden.
    unmapped = {key: value for key, value in requested.items() if key != "project_path"}
    outcome.observe("file_gen", {}, unmapped, project_root=workspace)
    assert outcome.missing_deliveries() == [wanted]
    assert outcome.unbacked_download_links(f"[Файл]({requested['download_url']})") == []
    download_only = TaskOutcome()
    download_only.observe("file_gen", {}, unmapped, project_root=workspace)
    restored_context = TaskOutcome(download_only.snapshot()).context(0)
    assert json.loads(restored_context.split("\n", 1)[1])["downloads"] == [{
        "status": "published", "download_url": requested["download_url"], "target": "",
    }]
    mapped = {**unmapped, "touched_path": "requested.csv"}
    outcome.observe("file_gen", {}, mapped, project_root=workspace, execution_status="rejected")
    assert outcome.missing_deliveries() == [wanted]
    outcome.observe("file_gen", {}, mapped, project_root=workspace)
    assert outcome.missing_deliveries() == []

    outcome.observe("resource_publish", {"project_path": "missing.csv"},
                    {"ok": False, "error": "source_not_file"}, project_root=workspace)
    _decide(outcome, workspace, delivery={"mode": "none", "targets": []})
    assert outcome.missing_deliveries() == []
    assert str(workspace / "missing.csv") in outcome.delivery_attempts
    assert "missing.csv" in outcome.context(0)
    assert outcome.unbacked_download_links("[Файл](/api/skills/download/missing.csv)")
    assert outcome.decision["delivery"]["mode"] == "none"


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
    assert not final["task_outcome"]["decision"]
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


def test_corrected_delivery_contract_resolves_typo_without_erasing_failed_attempt(workspace):
    (workspace / "report.csv").write_bytes(b"id,count\nA,5\n")
    replies = iter([
        _reply("resource_publish", {"project_path": "repotr.csv"}),
        _reply("resource_publish", {"project_path": "report.csv"}),
        _reply("runtime_control", {"operation": "task_decide", "config": {
            "disposition": "one_off", "reason": "Исправлена опечатка в имени результата",
            "delivery": {"mode": "chat_download", "targets": ["report.csv"]},
        }}),
        _reply(text="[Результат](/api/skills/download/report.csv)"),
    ])
    events = list(stream_code_agent(
        user_message="Опубликуй результат обработки.", project_root=workspace,
        run_id="delivery-typo", base_tools=["resource_publish", "runtime_control"],
        chat_fn=lambda **_kw: next(replies), auto_remember=False, permission_mode="bypass",
    ))
    final = next(event for event in events if event["type"] == "final_response")
    assert final["answer_status"] == "complete"
    assert not (workspace / "repotr.csv").exists()
    restored = TaskOutcome(RunJournal.load("delivery-typo").state["task_outcome"])
    assert restored.missing_deliveries() == []
    assert str(workspace / "repotr.csv") in restored.delivery_attempts
    assert "repotr.csv" in restored.context(0)


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
    _decide(outcome, workspace, delivery={"mode": "chat_download", "targets": ["transcript.txt"]})
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


@pytest.mark.parametrize("drift", ["unchanged", "target", "store", "missing", "unsafe_url", "wrong_target"])
def test_public_resume_rechecks_real_publication_without_republishing(workspace, drift):
    target = workspace / "report.csv"
    data = b"id,quantity\nA,3\n"
    target.write_bytes(data)
    replies = iter([
        _reply("runtime_control", {"operation": "task_decide", "config": {
            "disposition": "one_off", "reason": "Выдать готовый файл",
            "delivery": {"mode": "chat_download", "targets": ["report.csv"]},
        }}),
        _reply("resource_publish", {"project_path": "report.csv"}),
    ])
    stream = stream_code_agent(
        user_message="Дай готовый файл для скачивания.", project_root=workspace,
        run_id="publication-resume", base_tools=["runtime_control", "resource_publish"],
        chat_fn=lambda **_kw: next(replies), auto_remember=False, permission_mode="bypass",
    )
    try:
        for event in stream:
            if event.get("type") == "tool_call" and event.get("tool") == "resource_publish":
                assert event["ok"] is True
                assert RunJournal.load("publication-resume").state["task_outcome"]["deliveries"]
                break
        else:
            pytest.fail("No real publication receipt")
    finally:
        stream.close()
    stored = config.GENERATED_DIR / "report.csv"
    assert stored.read_bytes() == data
    if drift == "target":
        target.write_bytes(b"new unpublished result\n")
    elif drift == "store":
        stored.write_bytes(b"download overwritten elsewhere\n")
    elif drift == "missing":
        stored.unlink()
    elif drift in {"unsafe_url", "wrong_target"}:
        journal = RunJournal.load("publication-resume")
        state = json.loads(json.dumps(journal.state["task_outcome"]))
        if drift == "unsafe_url":
            state["deliveries"][str(target)]["download_url"] = str(workspace / "private.csv")
        else:
            other = workspace / "other.csv"
            other.write_bytes(data)
            state["deliveries"][str(target)]["target"] = str(other)
        journal.append_event({"type": "task_outcome_changed", "task_outcome": state})

    seen = []
    def finish(**kwargs):
        seen.append(kwargs["messages"])
        return _reply(text="Результат обработки сохранён.")

    events = list(stream_resume_session("publication-resume", chat_fn=finish))
    final = next(event for event in events if event["type"] == "final_response")
    assert not any(event.get("tool") == "resource_publish" for event in events)
    assert "Результат обработки сохранён" in final["text"]
    saved = RunJournal.load("publication-resume").state["task_outcome"]
    assert saved["decision"]["delivery"]["targets"] == [str(target)]
    assert final["answer_status"] == ("complete" if drift == "unchanged" else "degraded")
    if drift == "unchanged":
        assert len(seen) == 1
        assert TaskOutcome(saved).missing_deliveries() == []
    else:
        assert "report.csv" in final["text"]
        assert saved["deliveries"][str(target)]["status"] == "stale"
        # Observed failure is durable, even when old bytes return later.
        target.write_bytes(data)
        stored.write_bytes(data)
        assert TaskOutcome(saved).missing_deliveries() == [str(target)]


@pytest.mark.parametrize("delivery", [
    None, {"mode": "chat_download", "targets": []},
    {"mode": "none", "targets": ["unexpected.csv"]},
    {"mode": "chat_download", "targets": ["https://example.test/report.csv"]},
    {"mode": "chat_download", "targets": ["report.csv"], "sha256": "model assertion"},
])
def test_delivery_contract_rejects_invalid_or_model_supplied_evidence(workspace, delivery):
    with pytest.raises(ValueError):
        task_decide(workspace, {"disposition": "one_off", "reason": "Validate", "delivery": delivery})
