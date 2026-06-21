from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import run_code_agent, stream_code_agent  # noqa: E402
from app.application.code_agent.prompts import _shell_guidance  # noqa: E402
from app.application.code_agent.run_journal import RunJournal, discover_capabilities  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_run_journal_writes_atomic_state_events_health_and_redacts(tmp_path: Path) -> None:
    journal = RunJournal("test-run", runs_root=tmp_path / "runs")
    journal.start(
        {"project_root": str(tmp_path), "api_key": "do-not-store"},
        {"llm": {"available": True}},
    )
    journal.append_event({"type": "step_started", "step": 1})
    state = json.loads(journal.state_path.read_text(encoding="utf-8"))
    assert state["request"]["api_key"] == "[REDACTED]"
    assert state["last_successful_step"] == 1
    assert _read_jsonl(journal.events_path)[0]["type"] == "step_started"
    assert json.loads(journal.health_path.read_text(encoding="utf-8"))["status"] == "running"
    journal.finish(interrupted=True)
    assert not journal.lock_path.exists()
    assert not journal.agent_lock_path.exists()
    assert json.loads(journal.state_path.read_text(encoding="utf-8"))["resumable"] is True


def test_global_write_lock_blocks_concurrent_run_and_recovers_stale_lock(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    first = RunJournal("first", runs_root=runs_root)
    first.start({"user_message": "one"}, {"missing": []})
    second = RunJournal("second", runs_root=runs_root)
    try:
        second.start({"user_message": "two"}, {"missing": []})
    except RuntimeError as exc:
        assert "another write run" in str(exc)
    else:
        raise AssertionError("concurrent write run was not blocked")
    first.finish()

    second.agent_lock_path.write_text(
        json.dumps({"run_id": "dead", "pid": 999_999_999}),
        encoding="utf-8",
    )
    second.start({"user_message": "two"}, {"missing": []})
    assert "stale_lock_detected" in {
        event["type"] for event in _read_jsonl(second.events_path)
    }
    second.finish()


def test_partial_run_is_persisted_and_can_resume(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runs_root = tmp_path / "runs"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(runs_root))

    def looping_chat(**kwargs):
        if not kwargs.get("tools"):
            return {"message": {"content": "PARTIAL RESULT", "tool_calls": []}}
        return {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "glob", "arguments": {"pattern": "*"}}}],
            }
        }

    result = run_code_agent(
        user_message="inspect",
        project_root=project,
        model="test-model",
        max_steps=1,
        run_id="resume-test",
        chat_fn=looping_chat,
    )
    assert result["partial"] is True
    journal = RunJournal.load("resume-test", runs_root=runs_root)
    assert journal.state["resumable"] is True
    assert journal.commands_path.is_file()

    def finishing_chat(**_kwargs):
        return {"message": {"content": "Готово после продолжения", "tool_calls": []}}

    events = list(stream_code_agent(
        user_message="continue",
        project_root=project,
        model="test-model",
        max_steps=1,
        run_id="resume-test",
        chat_fn=finishing_chat,
        resume=True,
    ))
    assert events[0]["type"] == "run_resumed"
    assert events[-1]["type"] == "done"
    assert events[-1]["ok"] is True
    assert events[-1]["resumable"] is False
    final_state = RunJournal.load("resume-test", runs_root=runs_root).state
    assert final_state["status"] == "completed"
    assert final_state["resume_count"] == 1
    assert not (runs_root / "resume-test" / "run.lock").exists()
    assert "run_resumed" in {event["type"] for event in _read_jsonl(journal.events_path)}


def test_windows_shell_guidance_matches_run_bash_runtime() -> None:
    guidance = _shell_guidance("win32")
    assert "cmd.exe" in guidance
    assert "%USERPROFILE%\\.ssh\\config" in guidance
    assert "Не используй" in guidance


def test_capability_snapshot_marks_unconfigured_services_missing(monkeypatch) -> None:
    monkeypatch.setenv("LLAMA_SERVER_ENABLED", "false")
    monkeypatch.setenv("LOCAL_EMBED_ENABLED", "false")
    capabilities = discover_capabilities(model="local-model", tools=["read_file"])
    assert capabilities["llm"]["available"] is False
    assert capabilities["embedding"]["available"] is False
    assert capabilities["vision"]["available"] is False
    assert capabilities["web"]["available"] is True
    assert {"llm", "embedding", "vision"}.issubset(capabilities["missing"])
