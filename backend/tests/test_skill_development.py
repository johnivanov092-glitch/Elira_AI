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


def test_failed_skill_check_preserves_actionable_checker_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(development, "ROOT", tmp_path / "development")
    monkeypatch.setattr(task_skills, "SKILLS_ROOT", tmp_path / "installed")
    name = "checker-feedback"
    created = tool_runtime_control(tmp_path, operation="skill_create", name=name)["result"]
    directory = Path(created["directory"])
    (directory / "SKILL.md").write_text(
        "---\nname: checker-feedback\ndescription: Проверка результата обработки.\n---\n"
        "Выполни check.py и исправь обнаруженные несовпадения.\n",
        encoding="utf-8", newline="\n",
    )
    (directory / "check.py").write_text(
        "import sys\n"
        "print('row 7: expected signed quantity -2.000; got +2.000')\n"
        "print('AssertionError: return sign mismatch', file=sys.stderr)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8", newline="\n",
    )

    result = tool_runtime_control(tmp_path, operation="skill_check", name=name, config={
        "candidate_id": created["candidate_id"], "command": f'"{sys.executable}" check.py',
    })

    assert result["ok"] is False and result["status"] == "failed"
    assert result["error"]["message"] == "nonzero_exit"
    assert result["result"]["verification"]["exit_code"] == 1
    assert result["result"]["candidate_id"] == created["candidate_id"]
    # The serialized model-visible result must include both diagnostic streams.
    assert "expected signed quantity -2.000; got +2.000" in result["text"]
    assert "AssertionError: return sign mismatch" in result["text"]
    assert "STDOUT:" in result["text"] and "STDERR:" in result["text"]


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
    assert failed["error"]["message"] == "nonzero_exit"
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


def _recovery_fixture(tmp_path, monkeypatch, *, dependency=False):
    monkeypatch.setattr(development, "ROOT", tmp_path / "development")
    monkeypatch.setattr(task_skills, "SKILLS_ROOT", tmp_path / "installed")
    name = "source-recovery"

    def call(operation, **config):
        return tool_runtime_control(tmp_path, operation=operation, name=name, config=config)

    created = call("skill_create")["result"]
    directory = Path(created["directory"])
    (directory / "SKILL.md").write_text(
        "---\nname: source-recovery\ndescription: Compute a checked signed quantity.\n---\n"
        "Run engine.py and verify its result with check.py.\n", encoding="utf-8", newline="\n")
    (directory / "engine.py").write_text(
        "def net(shipped, returned):\n    return shipped - returned\n", encoding="utf-8", newline="\n")
    (directory / "obsolete.txt").write_text("old reference\n", encoding="utf-8", newline="\n")
    (directory / ".gitattributes").write_text("*.bin filter=unused text\n", encoding="utf-8", newline="\n")
    check = "from engine import net\nassert net(7, 2) == 5\nassert net(2, 7) == -5\n"
    if dependency:
        (directory / ".venv").mkdir()
        (directory / ".venv/dependency.dat").write_bytes(b"verified dependency\0")
        check += "from pathlib import Path\nassert Path('.venv/dependency.dat').read_bytes() == b'verified dependency\\0'\n"
    (directory / "check.py").write_text(check, encoding="utf-8", newline="\n")
    command = f'"{sys.executable}" check.py'
    assert call("skill_check", candidate_id=created["candidate_id"], command=command)["ok"]
    publication = call("skill_publish", candidate_id=created["candidate_id"])
    assert publication["ok"], publication
    return name, call, directory, publication["result"], command


def test_changed_publication_reports_exact_saved_revision_and_recovers_unverified_copy(tmp_path, monkeypatch):
    name, call, directory, published, command = _recovery_fixture(tmp_path, monkeypatch)
    active_path = development.ROOT / "active.json"
    active_before = active_path.read_bytes()
    receipt_path = development.ROOT / "receipts" / f"{published['candidate_id']}.json"
    receipt_before = receipt_path.read_bytes()
    (directory / "engine.py").write_text(
        "def net(shipped, returned):\n    return sum((shipped, -returned))\n", encoding="utf-8", newline="\n")
    (directory / "obsolete.txt").unlink()
    (directory / "verify_csv.py").write_text("assert 2 + 2 == 4\n", encoding="utf-8", newline="\n")
    added = {"verify_csv.py"}
    for index in range(12):
        filename = f"данные-{index:02}.bin"
        added.add(filename)
        (directory / filename).write_bytes(bytes([index, 0, 255, 13, 10]))
    # An interrupted later publication may have advanced HEAD; compare against
    # the receipt's revision, including binary source and literal attributes.
    repository = development.ROOT / "history" / name
    (repository / "package/engine.py").write_bytes((directory / "engine.py").read_bytes())
    development._git(repository, "add", "--force", "--", "package/engine.py")
    development._git(repository, "commit", "-m", "Unactivated history head")
    assert development._git(repository, "rev-parse", "HEAD") != published["revision"]

    with pytest.raises(development.PackageChanged) as failure:
        development.active_package(name)
    message = str(failure.value)
    assert "skill_create" in message and str(repository) in message and published["revision"] in message
    assert "данные-11.bin" not in message  # Bounded error; status holds all paths.
    assert len(message) < 2500
    status = call("skill_status")["result"]["integrity"]
    assert status["ok"] is False and status["source_diff"]["status"] == "available"
    assert set(status["source_diff"]["added"]) == added
    assert status["source_diff"]["changed"] == ["engine.py"]
    assert status["source_diff"]["deleted"] == ["obsolete.txt"]
    old_digest = development._digest(directory)
    recovered = call("skill_create")["result"]
    candidate = Path(recovered["directory"])
    assert recovered["source"]["candidate_id"] == published["candidate_id"]
    assert recovered["source"]["revision"] == published["revision"]
    assert recovered["source"]["integrity"] == "modified" and "UNVERIFIED" in recovered["warning"]
    receipt = json.loads((development.ROOT / "receipts" / f"{recovered['candidate_id']}.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "candidate" and not {"sha256", "revision", "check"} & receipt.keys()
    assert development._digest(candidate, include_dependencies=False) == recovered["source"]["observed_source_sha256"]
    assert development._digest(directory) == old_digest
    assert receipt_path.read_bytes() == receipt_before and active_path.read_bytes() == active_before
    assert not task_skills.skill_control("skill_load", name)["ok"]
    assert not call("skill_publish", candidate_id=recovered["candidate_id"])["ok"]
    assert call("skill_check", candidate_id=recovered["candidate_id"], command=command)["ok"]
    assert call("skill_publish", candidate_id=recovered["candidate_id"])["ok"]
    assert task_skills.skill_control("skill_load", name)["skill"]["directory"] == str(candidate)
    assert development._digest(directory) == old_digest
    assert "previous" not in json.loads(active_path.read_text(encoding="utf-8"))[name]


def test_dependency_only_drift_is_distinct_and_does_not_copy_a_verified_environment(tmp_path, monkeypatch):
    name, call, first_directory, first_publication, command = _recovery_fixture(tmp_path, monkeypatch, dependency=True)
    second = call("skill_create")["result"]
    directory = Path(second["directory"])
    (directory / ".venv").mkdir()
    (directory / ".venv/dependency.dat").write_bytes(b"verified dependency\0")
    (directory / "improvement.txt").write_text("second revision\n", encoding="utf-8", newline="\n")
    assert call("skill_check", candidate_id=second["candidate_id"], command=command)["ok"]
    published = call("skill_publish", candidate_id=second["candidate_id"])["result"]
    second_receipt = development.ROOT / "receipts" / f"{second['candidate_id']}.json"
    receipt_before = second_receipt.read_bytes()
    dependency = directory / ".venv/dependency.dat"
    dependency.write_bytes(b"modified dependency\0")
    status = call("skill_status")["result"]["integrity"]
    diff = status["source_diff"]
    assert diff["status"] == "available" and all(not diff[kind] for kind in ("added", "changed", "deleted"))
    with pytest.raises(development.PackageChanged, match="Git source is unchanged; installed dependencies or file metadata differ"):
        development.active_package(name)
    recovered = call("skill_create")["result"]
    candidate = Path(recovered["directory"])
    assert not (candidate / ".venv").exists()
    assert recovered["source"]["verified_package_sha256"] == published["sha256"]
    assert recovered["source"]["observed_package_sha256"] != published["sha256"]
    assert not call("skill_check", candidate_id=recovered["candidate_id"], command=command)["ok"]
    assert not call("skill_publish", candidate_id=recovered["candidate_id"])["ok"]
    (candidate / ".venv").mkdir()
    (candidate / ".venv/dependency.dat").write_bytes(b"verified dependency\0")
    assert call("skill_check", candidate_id=recovered["candidate_id"], command=command)["ok"]
    assert call("skill_publish", candidate_id=recovered["candidate_id"])["ok"]
    assert task_skills.skill_control("skill_load", name)["skill"]["directory"] == str(candidate)
    assert dependency.read_bytes() == b"modified dependency\0"
    assert second_receipt.read_bytes() == receipt_before
    state = json.loads((development.ROOT / "active.json").read_text(encoding="utf-8"))[name]
    assert state["previous"]["candidate_id"] == first_publication["candidate_id"]
    assert "previous" not in state["previous"]
    assert call("skill_rollback")["ok"]
    assert task_skills.skill_control("skill_load", name)["skill"]["directory"] == str(first_directory)
    assert dependency.read_bytes() == b"modified dependency\0"
    assert second_receipt.read_bytes() == receipt_before


def test_recovery_rejects_invalid_identity_redirect_and_copy_race_and_reports_missing_history(tmp_path, monkeypatch):
    name, call, directory, published, _command = _recovery_fixture(tmp_path, monkeypatch)
    active_path = development.ROOT / "active.json"
    active_before = active_path.read_bytes()
    active = json.loads(active_before)
    active[name]["sha256"] = "0" * 64
    active_path.write_text(json.dumps(active), encoding="utf-8", newline="\n")
    before_directories = sorted(directory.parent.iterdir())
    assert not call("skill_create")["ok"]
    assert sorted(directory.parent.iterdir()) == before_directories
    active_path.write_bytes(active_before)

    outside = tmp_path / "external.py"
    outside.write_text("external = True\n", encoding="utf-8", newline="\n")
    redirect = directory / "redirect.py"
    try:
        redirect.symlink_to(outside)
    except OSError:
        pass  # Windows without symlink privilege still tests identity and race.
    else:
        assert not call("skill_create")["ok"]
        assert sorted(directory.parent.iterdir()) == before_directories
        assert outside.read_text(encoding="utf-8") == "external = True\n"
        redirect.unlink()

    (directory / "added.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    repository = development.ROOT / "history" / name
    saved_history = repository.with_name(name + "-saved")
    repository.rename(saved_history)
    try:
        missing = call("skill_status")["result"]["integrity"]["source_diff"]
        assert missing["status"] == "unavailable" and "added" not in missing
        assert str(repository) == missing["repository"]
    finally:
        saved_history.rename(repository)
    original_copy = development.shutil.copy2
    changed = False

    def race(source, destination):
        nonlocal changed
        result = original_copy(source, destination)
        if not changed:
            changed = True
            with (directory / "engine.py").open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("# concurrently modified\n")
        return result

    receipts_before = sorted((development.ROOT / "receipts").iterdir())
    with monkeypatch.context() as scope:
        scope.setattr(development.shutil, "copy2", race)
        failed = call("skill_create")
    assert failed["ok"] is False and "Source changed while copying" in failed["error"]["message"]
    assert sorted(directory.parent.iterdir()) == before_directories
    assert sorted((development.ROOT / "receipts").iterdir()) == receipts_before
    assert active_path.read_bytes() == active_before
    assert not task_skills.skill_control("skill_load", name)["ok"]
