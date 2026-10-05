"""One real local lifecycle: author, execute, publish, reuse, improve, rollback."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.application.code_agent import skill_development as development, task_skills
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.tools import _shell
from app.application.code_agent.tools._runtime_control import tool_runtime_control


def test_python_environment_is_durable_isolated_and_pinned_through_rollback(tmp_path, monkeypatch):
    monkeypatch.setattr(development, "ROOT", tmp_path / "development")
    monkeypatch.setattr(task_skills, "SKILLS_ROOT", tmp_path / "installed")
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    external = tmp_path / "application-site"
    external.mkdir()
    (external / "app_only_dependency.py").write_text("VALUE = 42\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(external))
    # Neither bootstrap nor execution may bind to an inherited application home.
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "nonexistent-python-home"))
    for setting in ("PIP_TARGET", "PIP_PREFIX", "PIP_USER"):
        monkeypatch.setenv(setting, str(external))
    inherited_config = tmp_path / "application-pip.ini"
    inherited_config.write_text("[global]\ntarget = " + str(external) + "\n", encoding="utf-8")
    monkeypatch.setenv("PIP_CONFIG_FILE", str(inherited_config))
    name = "isolated-python"

    def create_and_publish():
        created = tool_runtime_control(tmp_path, operation="skill_create", name=name,
                                       config={"environment": "python"})
        assert created["ok"], created
        result = created["result"]
        directory = Path(result["directory"])
        environment = result["environment"]
        assert environment["status"] == "ready"
        assert Path(environment["python"]).is_file()
        assert Path(environment["directory"]).parent == directory
        assert environment["python_argv"] == [environment["python"], "-E", "-s"]
        assert environment["pip_argv"] == [*environment["python_argv"], "-m", "pip", "--isolated", "--require-virtualenv"]
        from app.application.code_agent.tools._run import tool_run_bash
        pip = tool_run_bash(directory, command=environment["pip_command"] + " --version")
        assert pip["ok"] and str(directory) in pip["text"], pip
        pip_config = tool_run_bash(directory, command=environment["pip_command"] + " config list")
        assert pip_config["ok"] and "global.target" not in pip_config["text"], pip_config
        assert os.environ["PIP_CONFIG_FILE"] == str(inherited_config)
        (directory / "SKILL.md").write_text(
            "---\nname: isolated-python\ndescription: Run a package-local Python capability.\n---\n"
            "Run check.py with the environment returned by skill_load.\n", encoding="utf-8", newline="\n")
        (directory / "helper.py").write_text("VALUE = 42\n", encoding="utf-8")
        (directory / "check.py").write_text(
            "import importlib.util, os, pathlib, sys\nfrom helper import VALUE\n"
            "assert VALUE == 42\nassert sys.prefix != sys.base_prefix\n"
            "assert pathlib.Path(sys.prefix).resolve() == pathlib.Path(__file__).parent.joinpath('.venv').resolve()\n"
            "assert importlib.util.find_spec('app_only_dependency') is None\n"
            "assert all(key not in os.environ for key in ('PYTHONHOME', 'PYTHONPATH', 'PIP_TARGET', 'PIP_PREFIX', 'PIP_USER'))\n"
            "assert os.environ['VIRTUAL_ENV'] == sys.prefix\nassert sys.flags.no_user_site\n"
            "assert os.environ['PIP_CONFIG_FILE'] == os.devnull\n"
            "print('isolated package check passed')\n", encoding="utf-8", newline="\n")
        checked = tool_runtime_control(tmp_path, operation="skill_check", name=name, config={
            "candidate_id": result["candidate_id"], "command": "python check.py && python -m pip --version"})
        assert checked["ok"], checked
        assert os.environ["PYTHONPATH"] == str(external)
        published = tool_runtime_control(tmp_path, operation="skill_publish", name=name,
                                         config={"candidate_id": result["candidate_id"]})
        assert published["ok"], published
        assert published["result"]["environment"] == environment
        return result, task_skills.skill_control("skill_load", name)["skill"]

    first, snapshot = create_and_publish()
    context = task_skills.SkillContext()
    context.activate(snapshot)
    assert context.snapshots()[0]["environment"] == first["environment"]
    assert json.dumps(first["environment"]["python"])[1:-1] in context.context()
    journal = RunJournal("isolated-package-pin")
    journal.start({"user_message": "Use isolated Python"}, {})
    try:
        journal.append_event({"type": "skills_changed", "active_skills": context.snapshots()})
    finally:
        journal.release()
    second, _ = create_and_publish()
    assert second["environment"]["python"] != first["environment"]["python"]
    resumed = task_skills.SkillContext()
    resumed.restore("isolated-package-pin")
    assert resumed.snapshots()[0]["environment"] == first["environment"]
    status = tool_runtime_control(tmp_path, operation="skill_status", name=name)["result"]
    assert status["integrity"]["environment"] == second["environment"]
    rollback = tool_runtime_control(tmp_path, operation="skill_rollback", name=name)
    assert rollback["ok"], rollback
    assert rollback["result"]["environment"] == first["environment"]
    assert task_skills.skill_control("skill_load", name)["skill"]["environment"] == first["environment"]


def test_python_environment_rejects_external_search_paths_and_system_packages(tmp_path, monkeypatch):
    monkeypatch.setattr(development, "ROOT", tmp_path / "development")
    monkeypatch.setattr(task_skills, "SKILLS_ROOT", tmp_path / "installed")
    created = tool_runtime_control(tmp_path, operation="skill_create", name="isolated-check",
                                   config={"environment": "python"})["result"]
    directory = Path(created["directory"])
    (directory / "SKILL.md").write_text(
        "---\nname: isolated-check\ndescription: Check isolated dependencies.\n---\nRun checks.\n",
        encoding="utf-8", newline="\n")
    environment = created["environment"]
    config = {"candidate_id": created["candidate_id"],
              "command": environment["python_command"] + ' -c "assert 2 + 2 == 4"'}
    cfg = Path(environment["directory"]) / "pyvenv.cfg"
    original = cfg.read_text(encoding="utf-8")
    cfg.write_text(original.replace("include-system-site-packages = false", "include-system-site-packages = true"),
                   encoding="utf-8", newline="\n")
    rejected = tool_runtime_control(tmp_path, operation="skill_check", name="isolated-check", config=config)
    assert not rejected["ok"] and "system-site-packages" in rejected["text"]
    cfg.write_text(original, encoding="utf-8", newline="\n")
    site = next(Path(environment["directory"]).rglob("site-packages"))
    pth = site / "external.pth"
    pth.write_text(str(tmp_path / "scratch-environment") + "\n", encoding="utf-8")
    rejected = tool_runtime_control(tmp_path, operation="skill_check", name="isolated-check", config=config)
    assert not rejected["ok"] and "external dependency" in rejected["text"]
    pth.write_text(str(directory) + "\n", encoding="utf-8")
    assert tool_runtime_control(tmp_path, operation="skill_check", name="isolated-check", config=config)["ok"]
    assert tool_runtime_control(tmp_path, operation="skill_publish", name="isolated-check", config=config)["ok"]
    pth.write_text(str(tmp_path / "scratch-environment") + "\n", encoding="utf-8")
    assert not task_skills.skill_control("skill_load", "isolated-check")["ok"]


def test_python_environment_bootstrap_failure_leaves_no_candidate_or_receipt(tmp_path, monkeypatch):
    from app.application.code_agent.tools import _run

    monkeypatch.setattr(development, "ROOT", tmp_path / "development")
    monkeypatch.setattr(task_skills, "SKILLS_ROOT", tmp_path / "installed")

    def fail_bootstrap(directory, *, command):
        assert " -I -m venv --copies " in command
        (directory / ".venv").mkdir()
        (directory / ".venv/partial.txt").write_text("incomplete", encoding="utf-8")
        return {"ok": False, "exit_code": 1, "text": "Unable to bootstrap bundled pip"}

    monkeypatch.setattr(_run, "tool_run_bash", fail_bootstrap)
    result = tool_runtime_control(tmp_path, operation="skill_create", name="failed-environment",
                                  config={"environment": "python"})
    assert not result["ok"] and "Unable to bootstrap bundled pip" in result["text"]
    assert not list((development.ROOT / "packages/failed-environment").iterdir())
    assert not (development.ROOT / "receipts").exists()


def test_legacy_published_environment_can_load_and_create_isolated_successor(tmp_path, monkeypatch):
    monkeypatch.setattr(development, "ROOT", tmp_path / "development")
    monkeypatch.setattr(task_skills, "SKILLS_ROOT", tmp_path / "installed")
    name = "legacy-python"
    created = tool_runtime_control(tmp_path, operation="skill_create", name=name,
                                   config={"environment": "python"})["result"]
    directory = Path(created["directory"])
    (directory / "SKILL.md").write_text(
        "---\nname: legacy-python\ndescription: Legacy package migration.\n---\nRun the saved checker.\n",
        encoding="utf-8", newline="\n")
    config = {"candidate_id": created["candidate_id"], "command": 'python -c "assert 2 + 2 == 4"'}
    assert tool_runtime_control(tmp_path, operation="skill_check", name=name, config=config)["ok"]
    assert tool_runtime_control(tmp_path, operation="skill_publish", name=name, config=config)["ok"]
    # Model the exact old receipt contract, before environment policy existed.
    cfg = directory / ".venv/pyvenv.cfg"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        "include-system-site-packages = false", "include-system-site-packages = true"),
        encoding="utf-8", newline="\n")
    site = next((directory / ".venv").rglob("site-packages"))
    (site / "legacy.pth").write_text(str(tmp_path / "legacy-dependencies") + "\n", encoding="utf-8")
    legacy_digest = development._digest(directory)
    receipt_path = development.ROOT / "receipts" / f"{created['candidate_id']}.json"
    receipt = development._read_json(receipt_path)
    receipt.pop("environment")
    receipt["sha256"] = legacy_digest
    development._write_json(receipt_path, receipt)
    state = development._read_json(development.ROOT / "active.json")
    state[name]["sha256"] = legacy_digest
    development._write_json(development.ROOT / "active.json", state)
    receipt_before = receipt_path.read_bytes()

    loaded = task_skills.skill_control("skill_load", name)
    assert loaded["ok"], loaded
    assert loaded["skill"]["environment"]["isolation"] == "legacy_unverified"
    successor = tool_runtime_control(tmp_path, operation="skill_create", name=name,
                                     config={"environment": "python"})
    assert successor["ok"], successor
    assert successor["result"]["environment"]["isolation"] == "validated"
    assert successor["result"]["environment"]["python"] != created["environment"]["python"]
    assert receipt_path.read_bytes() == receipt_before and development._digest(directory) == legacy_digest
    assert development._read_json(development.ROOT / "active.json") == state


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


def test_verified_digest_skips_reread_only_while_the_tree_is_unchanged(tmp_path, monkeypatch):
    # Review defect 6525c2e96257: the full .venv was re-hashed for every skill on every turn.
    import time

    directory = tmp_path / "package"
    (directory / ".venv").mkdir(parents=True)
    (directory / "SKILL.md").write_text("skill\n", encoding="utf-8", newline="\n")
    dependency = directory / ".venv" / "dependency.dat"
    dependency.write_bytes(b"verified dependency\0")
    past = time.time() - 60
    for path in [directory, *directory.rglob("*")]:
        os.utime(path, (past, past))
    verified = development._digest(directory)
    hashed: list[Path] = []
    real_digest = development._digest
    monkeypatch.setattr(development, "_digest", lambda path, **kw: hashed.append(path) or real_digest(path, **kw))

    assert development._verified_digest(directory, verified) == verified
    assert development._verified_digest(directory, verified) == verified
    assert len(hashed) == 1  # unchanged tree: content read once

    # A same-size rewrite with the old timestamp keeps the stat signature; the TTL bounds that window.
    dependency.write_bytes(b"modified dependency\0")
    os.utime(dependency, (past, past))
    monkeypatch.setattr(development, "_DIGEST_CACHE_TTL", 0.0)
    assert development._verified_digest(directory, verified) != verified
    monkeypatch.setattr(development, "_DIGEST_CACHE_TTL", 3600.0)

    # An ordinary edit changes the signature (and is within the racy window): always re-hashed.
    dependency.write_bytes(b"verified dependency\0")
    before = len(hashed)
    assert development._verified_digest(directory, verified) == verified
    assert development._verified_digest(directory, verified) == verified
    assert len(hashed) == before + 2  # fresh files are never trusted from the cache
