"""No builtin content processors; reusable synthetic skill runs through ordinary resource tools."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.application.code_agent.tools import _background_jobs, _run
from app.application.code_agent.tools._files import tool_read_file, tool_write_file
from app.application.code_agent.tools._resources import (
    tool_resource_materialize, tool_resource_process, tool_resource_publish,
)
from app.application.media import processing, resource_store
from app.core import config, data_files


@pytest.mark.parametrize("target", ["auto", "local_gpu", "local_cpu", "server_cpu", "server_gpu"])
def test_removed_transcription_is_unknown_before_resource_lookup(monkeypatch, target):
    monkeypatch.setattr(resource_store, "get_record", lambda _: pytest.fail("legacy STT resolved a resource"))
    output = tool_resource_process(resource_id="a" * 32, operation="transcribe", execution_target=target)
    assert output["ok"] is False and output["error"] == "unknown_operation"
    assert output["selected_target"] is None and output["backend"] is None


@pytest.mark.parametrize(("name", "kind"), [
    ("voice.ogg", "audio"), ("movie.mp4", "video"),
    ("voice.ogg", "document"), ("plain.txt", "audio"), ("plain.txt", "video"),
])
def test_document_extraction_never_routes_audio_or_video_to_generic_extractor(monkeypatch, name, kind):
    monkeypatch.setattr(resource_store, "read_bytes", lambda _: pytest.fail("audio resource bytes were read"))
    record = SimpleNamespace(original_name=name, kind=kind, resource_id="r-audio")
    output = processing.process_resource(record, "extract_text")
    assert output["ok"] is False and output["error"] == "unknown_operation"


def test_direct_processing_has_no_transcription_dispatch():
    record = SimpleNamespace(resource_id="r-audio")
    output = processing.process_resource(record, "transcribe", "local_gpu")
    assert output["ok"] is False and output["error"] == "unknown_operation"


@pytest.mark.parametrize("target", ["auto", "local_cpu"])
def test_inspect_keeps_cpu_result_contract_without_reading_contents(monkeypatch, target):
    record = resource_store.register_resource(
        original_name="note.txt", content_type="text/plain", owner_session="cpu-test",
        data="Содержание документа".encode("utf-8"))
    monkeypatch.setattr(resource_store, "read_bytes", lambda _: pytest.fail("inspect read contents"))
    try:
        output = tool_resource_process(record.resource_id, "inspect", target)
    finally:
        resource_store.discard(record)
    assert output["ok"] is True
    assert output["requested_target"] == target
    assert output["selected_target"] == output["execution_target"] == "local_cpu"
    assert output["backend"] == "resource-inspect"
    assert record.storage_path not in str(output)
    assert "Содержание документа" not in str(output)
    assert ("fallback_chain" in output) == (target == "auto")


def test_remaining_resource_arguments_fail_closed_without_echoing_input(monkeypatch):
    monkeypatch.setattr(resource_store, "get_record", lambda _: pytest.fail("invalid call resolved a resource"))
    output = tool_resource_process("a" * 32, "inspect", "local_gpu")
    assert output["error"] == "invalid_execution_target"
    output = tool_resource_process("a" * 32, "inspect", path="PRIVATE_PATH")
    assert output["error"] == "unsupported_arguments"
    assert "PRIVATE_PATH" not in str(output)


def test_mutable_skill_materialize_job_and_publish_use_current_script(tmp_path, monkeypatch):
    # Synthetic processor: this verifies the ordinary-tool path, not GPU or ASR quality.
    project = tmp_path / "project"
    project.mkdir()
    data = tmp_path / "data"
    skill = data / "skills/audio-transcribe"
    generated = data / "generated"
    generated.mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "GENERATED_DIR", generated)
    monkeypatch.setattr(data_files, "DATA_DIR", data)
    monkeypatch.setattr(_background_jobs, "_state_dir", lambda: data / "background_jobs")
    instructions = tool_write_file(project, path=str(skill / "SKILL.md"), content="Use transcribe.py.\n")
    assert instructions["ok"] and "transcribe.py" in tool_read_file(project, path=str(skill / "SKILL.md"))["text"]
    record = resource_store.register_resource(
        original_name="voice.ogg", content_type="audio/ogg", owner_session="skill-test", data=b"OggS synthetic")
    try:
        materialized = tool_resource_materialize(project, record.resource_id)
        assert materialized["ok"]
        source = project / materialized["project_path"]
        assert source.read_bytes() == b"OggS synthetic"
        for version in ("first", "edited"):
            script = (
                "from pathlib import Path\nimport sys\n"
                f"Path(sys.argv[2]).write_text({version!r} + ':' + Path(sys.argv[1]).read_bytes().decode('ascii'), encoding='utf-8')\n"
                "print('processor completed', flush=True)\n"
            )
            assert tool_write_file(project, path=str(skill / "transcribe.py"), content=script)["ok"]
            destination = project / f"transcript-{version}.txt"
            started = _run.tool_run_server(project, action="start", kind="job", command=(
                f'"{sys.executable}" "{skill / "transcribe.py"}" "{source}" "{destination}"'))
            assert started["ok"], started
            try:
                logs = _run.tool_run_server(project, action="logs", kind="job", pid=int(started["pid"]), wait_seconds=10)
                assert logs["status"] == "completed", logs
                assert "processor completed" in logs["text"]
            finally:
                _run.tool_run_server(project, action="stop", kind="job", pid=int(started["pid"]))
            assert destination.read_text(encoding="utf-8") == version + ":OggS synthetic"
            published = tool_resource_publish(project, project_path=destination.name)
            assert published["ok"]
            delivered = generated / published["download_name"]
            assert delivered.read_bytes() == destination.read_bytes()
            assert published["sha256"] == hashlib.sha256(delivered.read_bytes()).hexdigest()
    finally:
        resource_store.discard(record)


@pytest.mark.parametrize("operation", ["extract_text", "transcribe"])
def test_all_removed_operations_fail_before_resource_lookup(monkeypatch, operation):
    monkeypatch.setattr(resource_store, "get_record", lambda _: pytest.fail("removed operation resolved resource"))
    result = tool_resource_process("a" * 32, operation)
    assert result["ok"] is False and result["error"] == "unknown_operation"
