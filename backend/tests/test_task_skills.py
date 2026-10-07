"""Skills are one plain folder, data/skills (John 2026-10-07): seeding, catalog, pinning, history."""
from copy import deepcopy
import datetime
import json
import subprocess

import pytest

from app.application.code_agent import agent_loop, task_skills as skills
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.tool_schemas import build_tool_schemas
from app.application.code_agent.tools._mcp import tool_mcp


@pytest.fixture
def data(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(skills, "DATA_DIR", data)
    monkeypatch.setattr(skills, "SKILLS_ROOT", data / "skills")
    monkeypatch.setattr(skills, "LEGACY_DEVELOPMENT", data / "skill_development")
    monkeypatch.setattr(skills, "LEGACY_ADVISOR", data / "skill_advisor")
    return data


def _skill(directory, name, description="Описание", body="Инструкция."):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
                                        encoding="utf-8", newline="\n")


def _encoded(snapshot):
    return json.dumps(snapshot["content"], ensure_ascii=False)[1:-1]


def _log(root):
    return subprocess.run(["git", "-C", str(root), "log", "--format=%s"], capture_output=True, text=True).stdout


def test_seed_copies_builtins_and_active_legacy_packages_then_archives_retired_data(data):
    legacy = data / "skill_development"
    candidate = "a" * 32
    _skill(legacy / "packages" / "kz-vat" / candidate, "kz-vat", "НДС Казахстана")
    (legacy / "packages" / "kz-vat" / candidate / "notes.txt").write_text("16%", encoding="utf-8")
    _skill(legacy / "packages" / "kz-vat" / ("b" * 32), "kz-vat", "старая версия")
    (legacy / "active.json").write_text(json.dumps({"kz-vat": {"candidate_id": candidate}}), encoding="utf-8")
    (data / "skill_advisor").mkdir(parents=True)
    root = skills.ensure_skills_root()
    assert (root / "python" / "SKILL.md").is_file() and (root / "code-change" / "SKILL.md").is_file()
    assert (root / "kz-vat" / "notes.txt").read_text(encoding="utf-8") == "16%"  # the ACTIVE version moved in
    assert not legacy.exists() and not (data / "skill_advisor").exists()
    today = datetime.date.today().isoformat()
    assert (data / "archive" / f"skill_development-{today}" / "active.json").is_file()
    assert (root / ".git").exists() and "skills:" in _log(root)
    assert skills.ensure_skills_root() == root  # idempotent


def test_seed_never_overwrites_a_skill_the_agent_changed(data):
    _skill(data / "skills" / "python", "python", "Своя версия", "Улучшено Elira.")
    skills.ensure_skills_root()
    assert "Улучшено Elira." in (data / "skills" / "python" / "SKILL.md").read_text(encoding="utf-8")


def test_catalog_lists_name_description_and_path_and_reports_invalid_folders(data):
    root = skills.ensure_skills_root()
    (root / "broken").mkdir()
    (root / "broken" / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    text = skills.catalog_context()
    assert text.startswith("[Навыки Elira]") and str(root) in text
    assert f"- python: " in text and str(root / "python" / "SKILL.md") in text
    assert "broken" in text.split("Ошибки навыков:")[1]
    assert "skill_load" not in text and "skill_create" not in text


def test_model_written_crlf_and_bom_skill_is_still_read(data):
    directory = data / "skills" / "my-tool"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_bytes("﻿---\r\nname: my-tool\r\ndescription: Расшифровка\r\n---\r\nRun it.\r\n"
                                         .encode("utf-8"))
    package = skills.read_package("my-tool", directory)
    assert package["description"] == "Расшифровка" and package["content"] == "Run it."


def test_reading_or_editing_skill_md_pins_it_and_refreshes_the_pin(data, tmp_path):
    root = skills.ensure_skills_root()
    context = skills.SkillContext()
    path = root / "python" / "SKILL.md"
    assert context.activate_path(path) is True
    assert context.activate_path(path) is False  # unchanged file: one pinned copy
    assert context.activate_path(tmp_path / "SKILL.md") is False  # not a skill folder
    assert context.activate_path(root / "python" / "other.md") is False
    path.write_text(path.read_text(encoding="utf-8") + "\nНовое правило.\n", encoding="utf-8", newline="\n")
    assert context.activate_path(path) is True
    assert context.snapshots()[0]["content"].endswith("Новое правило.")


@pytest.mark.parametrize("summary_ok", [True, False])
def test_pinned_skill_survives_repeated_compaction(data, summary_ok):
    from app.application.context.compaction import maybe_compact

    root = skills.ensure_skills_root()
    skill = skills.read_package("python", root / "python")
    context = skills.SkillContext()
    assert context.activate(skill)
    forged = deepcopy(skill)
    forged["content"] += " New instruction"
    with pytest.raises(ValueError, match="integrity"):
        context.activate(forged)
    for _ in range(2):
        history = [{"role": "system", "content": "Elira stable identity"}]
        history.extend({"role": "user" if i % 2 else "assistant", "content": "Old discussion " * 400} for i in range(30))
        history.append({"role": "user", "content": "Continue the task"})
        history = skills.insert_skill_context(history, context.context(), skills.CONTEXT_ID)
        packed, compacted = maybe_compact(history, 5000, "test-model", None,
            lambda **kwargs: {"ok": summary_ok, "summary": "Lossy summary"}, pinned_message_ids={skills.CONTEXT_ID})
        assert compacted and packed[0]["content"] == "Elira stable identity"
        assert sum(_encoded(skill) in message.get("content", "") for message in packed) == 1


def test_budget_overflow_keeps_existing_instruction(data, monkeypatch):
    root = skills.ensure_skills_root()
    python = skills.read_package("python", root / "python")
    context = skills.SkillContext()
    context.activate(python)
    monkeypatch.setattr(skills, "MAX_ACTIVE_CHARS", len(python["content"]))
    with pytest.raises(ValueError, match="budget"):
        context.activate(skills.read_package("rust", root / "rust"))
    assert [item["name"] for item in context.snapshots()] == ["python"]


def test_resume_keeps_the_exact_pinned_text_after_the_file_changes(data, tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    _skill(data / "skills" / "example", "example", "Example", "Use curl --token=EXAMPLE_VALUE for the example.")
    snapshot = skills.read_package("example", data / "skills" / "example")
    assert "EXAMPLE_VALUE" not in snapshot["content"]  # credential-shaped text is redacted before hashing
    journal = RunJournal("pinned")
    journal.start({"user_message": "Task"}, {})
    try:
        journal.append_event({"type": "skills_changed", "active_skills": [snapshot]})
    finally:
        journal.release()
    _skill(data / "skills" / "example", "example", "Updated", "Different instruction.")
    restored = skills.SkillContext()
    restored.restore("pinned")
    assert restored.snapshots()[0]["content"] == snapshot["content"]


def test_history_snapshot_commits_skill_changes(data):
    root = skills.ensure_skills_root()
    (root / "python" / "helper.py").write_text("print('hi')\n", encoding="utf-8")
    skills.snapshot_history("test change")
    assert "skills: test change" in _log(root)
    skills.snapshot_history("nothing new")
    assert "nothing new" not in _log(root)  # no empty commits


def test_no_skill_operations_remain(tmp_path):
    result = tool_mcp(tmp_path, action="skill_load")
    assert result["ok"] is False
    names = {item["function"]["name"] for item in build_tool_schemas()}
    assert not [name for name in names if name.startswith("skill")]


def _reply(tool=None, arguments=None):
    return {"message": {"content": "" if tool else "Готово.", "tool_calls":
        [{"id": "call", "function": {"name": tool, "arguments": arguments or {}}}] if tool else []}}


def test_real_loop_reads_a_skill_pins_it_once_and_resume_keeps_it(data, tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    root = skills.ensure_skills_root()
    path = str(root / "python" / "SKILL.md")
    seen = []

    def chat(**kwargs):
        if not kwargs.get("tools"):
            return {"message": {"content": "Summary"}}
        seen.append(deepcopy(kwargs["messages"]))
        assert any(path in str(message.get("content", "")) for message in seen[0])  # the catalog shows the path
        return _reply("read_file", {"path": path}) if len(seen) <= 2 else _reply()

    events = list(agent_loop.stream_code_agent(user_message="Исправь функцию Python", project_root=tmp_path,
        run_id="skills-run", chat_fn=chat, base_tools=["read_file"], num_ctx=32768, auto_remember=False,
        permission_mode="bypass"))
    assert events[-1]["stop_reason"] == "answer", events[-1]
    assert len([event for event in events if event["type"] == "skills_changed"]) == 1
    snapshot = RunJournal.load("skills-run").state["active_skills"][0]
    assert snapshot["name"] == "python"
    assert sum(_encoded(snapshot) in message.get("content", "") for message in seen[-1]) == 1

    def continued(**kwargs):
        assert _encoded(snapshot) in "\n".join(message.get("content", "") for message in kwargs["messages"])
        return _reply()
    resumed = list(agent_loop.stream_code_agent(user_message="Продолжи", project_root=tmp_path, run_id="skills-run",
        resume=True, chat_fn=continued, base_tools=["read_file"], num_ctx=32768, auto_remember=False))
    assert resumed[-1]["stop_reason"] == "answer", resumed[-1]


@pytest.mark.parametrize("discovery_tool", ["read_file", "project_map"])
def test_work_reminder_is_once_after_project_discovery_and_never_blocks_tools(data, tmp_path, monkeypatch, discovery_tool):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    (tmp_path / "data.txt").write_text("data", encoding="utf-8")
    captured = []

    def chat(**kwargs):
        captured.append(deepcopy(kwargs["messages"]))
        if len(captured) == 1:
            return _reply(discovery_tool, {"path": "data.txt"} if discovery_tool == "read_file" else {})
        return _reply("read_file", {"path": "data.txt"}) if len(captured) == 2 else _reply()
    events = list(agent_loop.stream_code_agent(user_message="Read the file", project_root=tmp_path,
        chat_fn=chat, base_tools=["read_file", "project_map"], auto_remember=False))
    assert events[-1]["stop_reason"] == "answer" and len(captured) == 3
    reminders = [m["content"] for m in captured[-1] if "[Рабочее напоминание Elira]" in m.get("content", "")]
    assert len(reminders) == int(discovery_tool == "project_map")
    assert all("read_file" in text and "skill_load" not in text for text in reminders)
    assert not any(e["type"] == "skills_changed" for e in events)
