from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

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
    journal.append_event({
        "type": "usage",
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
        "cached_prompt_tokens": 80,
        "prompt_tokens_per_second": 400.0,
        "tokens_per_second": 18.5,
        "context": {
            "current_tokens": 900,
            "reserved_output_tokens": 200,
            "free_tokens": 3000,
        },
        "untrusted_payload": {"prompt_tokens": "Bearer do-not-store"},
        "access_token": "do-not-store",
    })
    state = json.loads(journal.state_path.read_text(encoding="utf-8"))
    assert state["request"]["api_key"] == "[REDACTED]"
    assert state["last_successful_step"] == 1
    assert _read_jsonl(journal.events_path)[0]["type"] == "step_started"
    usage = _read_jsonl(journal.events_path)[1]
    assert usage["prompt_tokens"] == 120
    assert usage["completion_tokens"] == 30
    assert usage["total_tokens"] == 150
    assert usage["cached_prompt_tokens"] == 80
    assert usage["prompt_tokens_per_second"] == 400.0
    assert usage["tokens_per_second"] == 18.5
    assert usage["context"]["current_tokens"] == 900
    assert usage["context"]["reserved_output_tokens"] == 200
    assert usage["context"]["free_tokens"] == 3000
    assert usage["untrusted_payload"]["prompt_tokens"] == "[REDACTED]"
    assert usage["access_token"] == "[REDACTED]"
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
    assert capabilities["vision"]["available"] is True
    assert capabilities["web"]["available"] is True
    assert {"llm", "embedding"}.issubset(capabilities["missing"])
    assert "vision" not in capabilities["missing"]
