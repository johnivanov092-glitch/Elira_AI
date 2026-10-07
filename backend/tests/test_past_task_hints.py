"""Similar past tasks at run start (John 2026-10-07)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.application.code_agent import run_journal
from app.application.code_agent.run_journal import past_task_hints


def _run(runs: Path, run_id: str, *, task: str | None, root: Path, status: str = "completed",
         files: list[str] = (), created: str = "2026-10-06T10:00:00+00:00", skills: list[str] = ("weather",)) -> None:
    request = {"user_message": task or "delegated instruction", "project_root": str(root)}
    if task is not None:
        request["memory_query"] = task
    (runs / run_id).mkdir(parents=True)
    (runs / run_id / "state.json").write_text(json.dumps({
        "run_id": run_id, "status": status, "created_at": created, "project_root": str(root),
        "request": request, "changed_files": list(files), "mutated_files": list(files),
        "active_skills": [{"name": name} for name in skills],
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(autouse=True)
def _fresh_cache():
    run_journal._PAST_TASKS.clear()
    yield
    run_journal._PAST_TASKS.clear()


def test_similar_task_lists_existing_files_only(tmp_path: Path) -> None:
    runs, sandbox = tmp_path / "runs", tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "transcript_all.txt").write_text("[00:00 → 00:02] Привет.", encoding="utf-8")
    _run(runs, "old", task="сделай мне расшифровку каждого файла, используй локальный gpu",
         root=sandbox, status="cancelled", files=["transcript_all.txt", "transcribe_gpu.py"], skills=[])

    text = past_task_hints("расшифруй голосовые в текст на локальном gpu",
                           same_place=lambda root: Path(root) == sandbox, runs_root=runs)

    assert text.startswith("[Похожие прошлые задачи пользователя")
    assert "2026-10-06 «сделай мне расшифровку каждого файла" in text
    assert "остановлена" in text
    assert str(sandbox / "transcript_all.txt") in text
    assert "transcribe_gpu.py" not in text


def test_unrelated_foreign_delegated_and_current_runs_are_skipped(tmp_path: Path) -> None:
    runs, here, other = tmp_path / "runs", tmp_path / "here", tmp_path / "other"
    task = "расшифровка голосовых сообщений локально"
    _run(runs, "current", task=task, root=here)
    _run(runs, "foreign", task=task, root=other)
    _run(runs, "child", task=None, root=here)
    _run(runs, "weather", task="погода в Алматы на неделю", root=here)

    text = past_task_hints(task, same_place=lambda root: Path(root) == here,
                           exclude_run_id="current", runs_root=runs)

    assert text == ""


def test_at_most_three_hints_and_duplicates_collapse(tmp_path: Path) -> None:
    runs, here = tmp_path / "runs", tmp_path / "here"
    for index in range(5):
        _run(runs, f"run{index}", task=f"погода в Алматы на неделю, вариант {index}", root=here,
             created=f"2026-10-0{index + 1}T10:00:00+00:00")
    _run(runs, "dup", task="погода в Алматы на неделю, вариант 4", root=here)

    text = past_task_hints("какая погода в Алматы на неделю", same_place=lambda root: True, runs_root=runs)

    lines = text.splitlines()[1:]
    assert len(lines) == 3
    assert sum("вариант 4" in line for line in lines) == 1


def test_a_past_task_without_files_or_skill_is_not_a_hint(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _run(runs, "qa", task="Как сказать на английском «консольные инвалиды»?", root=tmp_path, skills=[])
    _run(runs, "gone", task="Как сказать на английском «консольные игроки»?", root=tmp_path,
         files=["moved.txt"], skills=[])

    assert past_task_hints("Как сказать на английском «консольные инвалиды»?",
                           same_place=lambda root: True, runs_root=runs) == ""


def test_short_or_generic_requests_get_no_hint(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _run(runs, "hello", task="привет, как дела?", root=tmp_path)

    assert past_task_hints("привет, как дела?", same_place=lambda root: True, runs_root=runs) == ""


def test_new_request_carries_hints_in_the_catalog_block(tmp_path: Path, monkeypatch) -> None:
    from app.application.code_agent.task_skills import CATALOG_ID
    from app.application.code_agent.turn_context import TurnContext

    runs, root = tmp_path / "runs", tmp_path / "project"
    root.mkdir()
    (root / "sales.csv").write_text("month,total\n", encoding="utf-8")
    _run(runs, "old", task="собери отчёт по продажам за сентябрь в таблицу", root=root, files=["sales.csv"], skills=[])
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(runs))
    messages = [{"role": "system", "content": "system"},
                {"role": "user", "content": "отчёт по продажам за октябрь в таблицу"}]

    fresh = TurnContext(messages=list(messages), raw_user_message=messages[1]["content"],
                        root=root, working_dir=None, run_id="new")
    fresh.initialize_skills(resume=False)
    resumed = TurnContext(messages=list(messages), raw_user_message=messages[1]["content"],
                          root=root, working_dir=None, run_id="new")
    resumed.initialize_skills(resume=False)

    def catalog(context: TurnContext) -> str:
        return "\n".join(str(message.get("content")) for message in context.messages
                         if message.get("_msg_id") == CATALOG_ID or CATALOG_ID in str(message.get("_msg_id", "")))

    assert "собери отчёт по продажам за сентябрь" in "\n".join(str(m.get("content")) for m in fresh.messages)
    assert catalog(fresh) == catalog(resumed)

    ongoing = TurnContext(messages=[messages[0], {"role": "user", "content": "привет"},
                                    {"role": "assistant", "content": "Привет!"}, messages[1]],
                          raw_user_message=messages[1]["content"], root=root, working_dir=None, run_id="next")
    ongoing.initialize_skills(resume=False)
    assert "собери отчёт по продажам" not in "\n".join(str(m.get("content")) for m in ongoing.messages)
