"""One real local lifecycle: author, execute, publish, reuse, improve, rollback."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.application.code_agent import skill_development as development, task_skills
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.tools import _shell
from app.application.code_agent.tools._runtime_control import tool_runtime_control


def test_real_skill_lifecycle_keeps_active_version_until_verified_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(development, "ROOT", tmp_path / "development")
    monkeypatch.setattr(task_skills, "SKILLS_ROOT", tmp_path / "installed")
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    name = "transcript-cleanup"

    def call(operation, **config):
        return tool_runtime_control(tmp_path, operation=operation, name=name, config=config)

    first = call("skill_create")["result"]
    directory = Path(first["directory"])
    (directory / "SKILL.md").write_text(
        "---\nname: transcript-cleanup\ndescription: Нормализация пробелов в готовой расшифровке.\n---\n"
        "Запусти normalize.py отдельным процессом и проверь весь результат.\n", encoding="utf-8", newline="\n",
    )
    (directory / "normalize.py").write_text(
        "def normalize(text):\n    return ' '.join(text.split())\n", encoding="utf-8", newline="\n",
    )
    (directory / "check.py").write_text(
        "from normalize import normalize\nassert normalize('один  два\\nтри') == 'один два три'\n",
        encoding="utf-8", newline="\n",
    )
    (directory / ".gitignore").write_text("*.py\n.venv/\n", encoding="utf-8", newline="\n")
    dependency = directory / ".venv" / "dependency.dat"
    dependency.parent.mkdir()
    dependency.write_bytes(b"verified dependency\x00v1")
    if sys.platform != "win32":
        # A standard Unix venv interpreter link remains usable and is hashed.
        (dependency.parent / "python").symlink_to(Path(sys.executable).resolve())
        (dependency.parent / "lib").mkdir()
        (dependency.parent / "lib64").symlink_to("lib", target_is_directory=True)
    candidate = first["candidate_id"]
    assert not call("skill_publish", candidate_id=candidate)["ok"]
    checked = call("skill_check", candidate_id=candidate, command=f'"{sys.executable}" check.py')
    assert checked["ok"], checked
    assert task_skills.discover_skills()["skills"] == []
    published = call("skill_publish", candidate_id=candidate)
    assert published["ok"], published
    loaded = task_skills.skill_control("skill_load", name)
    assert loaded["ok"] and loaded["skill"]["directory"] == str(directory)
    snapshot = deepcopy(loaded["skill"])
    assert snapshot["candidate_id"] == candidate
    assert snapshot["package_sha256"] == published["result"]["sha256"]
    journal = RunJournal("published-skill")
    journal.start({"user_message": "Use this exact skill"}, {})
    try:
        journal.append_event({"type": "skills_changed", "active_skills": [snapshot]})
    finally:
        journal.release()
    repo = development.ROOT / "history" / name
    assert subprocess.check_output(["git", "-C", str(repo), "remote"], text=True) == ""
    assert subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True) == ""
    committed = subprocess.check_output(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "HEAD"], text=True)
    assert "package/normalize.py" in committed  # Even though package .gitignore excludes *.py.
    assert ".venv" not in committed

    second = call("skill_create")["result"]
    changed = Path(second["directory"])
    next_id = second["candidate_id"]
    assert not (changed / ".venv").exists()
    # Failure cannot replace the working version or its on-disk scripts.
    (changed / "normalize.py").write_text("def normalize(text):\n    return ''\n", encoding="utf-8", newline="\n")
    failed = call("skill_check", candidate_id=next_id, command=f'"{sys.executable}" check.py')
    assert not failed["ok"]
    assert "verification_failed" in failed["error"]["message"]
    assert task_skills.skill_control("skill_load", name)["skill"]["directory"] == str(directory)
    (changed / "normalize.py").write_text(
        "def normalize(text):\n    return ' '.join(text.strip().split())\n", encoding="utf-8", newline="\n",
    )
    changed_dependency = changed / ".venv" / "dependency.dat"
    changed_dependency.parent.mkdir()
    changed_dependency.write_bytes(b"verified dependency\x00v2")
    assert call("skill_check", candidate_id=next_id, command=f'"{sys.executable}" check.py')["ok"]
    (changed / "new-file.txt").write_text("changed after verification", encoding="utf-8")
    assert not call("skill_publish", candidate_id=next_id)["ok"]
    assert call("skill_check", candidate_id=next_id, command=f'"{sys.executable}" check.py')["ok"]
    assert call("skill_publish", candidate_id=next_id)["ok"]
    assert task_skills.skill_control("skill_load", name)["skill"]["directory"] == str(changed)

    # Resume binds old instructions to the old scripts, never the new active directory.
    restored = task_skills.SkillContext()
    restored.restore("published-skill")
    assert restored.snapshots()[0]["directory"] == str(directory)
    assert restored.snapshots()[0]["candidate_id"] == candidate
    assert restored.snapshots()[0]["revision"] == snapshot["revision"]
    for field, value in (
        ("directory", str(tmp_path / "unowned")),
        ("candidate_id", next_id),
        ("package_sha256", "0" * 64),
    ):
        forged = {**snapshot, field: value}
        with pytest.raises(ValueError):
            task_skills.SkillContext().activate(forged)
    forged = {**snapshot, "content": "Unverified changed instructions"}
    forged["sha256"] = hashlib.sha256(forged["content"].encode()).hexdigest()
    with pytest.raises(ValueError, match="package receipt"):
        task_skills.SkillContext().activate(forged)

    # Modifying either source or installed dependencies invalidates loading.
    for target in (changed / "normalize.py", changed_dependency):
        original = target.read_bytes()
        target.write_bytes(original + b"tampered")
        assert not task_skills.skill_control("skill_load", name)["ok"]
        target.write_bytes(original)
        assert task_skills.skill_control("skill_load", name)["ok"]
    old_dependency = dependency.read_bytes()
    dependency.write_bytes(b"changed old interpreter dependency")
    with pytest.raises(ValueError, match="dependencies changed"):
        task_skills.SkillContext().restore("published-skill")
    dependency.write_bytes(old_dependency)

    # Stop between a completed Git commit and publication cannot change active.json.
    cancelled_candidate = call("skill_create")["result"]
    cancelled_id = cancelled_candidate["candidate_id"]
    assert call("skill_check", candidate_id=cancelled_id, command=f'"{sys.executable}" check.py')["ok"]
    original_git = development._git
    run_id = "cancel-skill-publication"
    token = _shell.set_current_run_id(run_id)
    try:
        def cancel_after_commit(repository, *arguments):
            result = original_git(repository, *arguments)
            if arguments[0] == "commit":
                _shell.cancel_run_callbacks(run_id)
            return result

        with monkeypatch.context() as scope:
            scope.setattr(development, "_git", cancel_after_commit)
            cancelled = call("skill_publish", candidate_id=cancelled_id)
        assert cancelled["status"] == "cancelled", cancelled
    finally:
        _shell.clear_run_stop_marker(run_id)
        _shell.reset_current_run_id(token)
    assert task_skills.skill_control("skill_load", name)["skill"]["directory"] == str(changed)
    receipt = json.loads((development.ROOT / "receipts" / f"{cancelled_id}.json").read_text(encoding="utf-8"))
    assert "revision" not in receipt
    assert call("skill_discard", candidate_id=cancelled_id)["ok"]

    assert call("skill_rollback")["ok"]
    assert task_skills.skill_control("skill_load", name)["skill"]["directory"] == str(directory)
    assert not call("skill_check", candidate_id=candidate, command=f'"{sys.executable}" check.py')["ok"]
    assert not call("skill_publish", candidate_id="../escape")["ok"]
    assert not call("skill_discard", candidate_id=candidate)["ok"]
    discarded = call("skill_create")["result"]
    assert call("skill_discard", candidate_id=discarded["candidate_id"])["ok"]
    assert not Path(discarded["directory"]).exists()

    # A redirect in the managed history must not initialize/modify its target.
    other_name = "redirected"
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (development.ROOT / "history" / other_name).symlink_to(outside, target_is_directory=True)
    except OSError:
        return  # Windows accounts without symlink privilege still exercise the lifecycle above.
    candidate_result = tool_runtime_control(tmp_path, operation="skill_create", name=other_name)["result"]
    other_directory = Path(candidate_result["directory"])
    (other_directory / "SKILL.md").write_text(
        "---\nname: redirected\ndescription: Check managed history.\n---\nRun a local check.\n",
        encoding="utf-8", newline="\n",
    )
    config = {"candidate_id": candidate_result["candidate_id"], "command": f'"{sys.executable}" -c "assert 2 + 2 == 4"'}
    assert tool_runtime_control(tmp_path, operation="skill_check", name=other_name, config=config)["ok"]
    assert not tool_runtime_control(tmp_path, operation="skill_publish", name=other_name, config=config)["ok"]
    assert list(outside.iterdir()) == []
