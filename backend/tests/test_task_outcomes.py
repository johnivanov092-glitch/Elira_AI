"""Real checker subprocesses and durable task-result receipts, without an LLM."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.task_outcomes import TaskOutcome, file_digest, result_verify, task_decide


def _command(root: Path, body: str) -> str:
    script = root / f"checker_{uuid.uuid4().hex}.py"
    script.write_text("import json\nfrom pathlib import Path\n" + body + "\n",
                      encoding="utf-8", newline="\n")
    argv = [sys.executable, str(script)]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _report(checks: list[dict]) -> str:
    return (
        "Path('report.json').write_text("
        f"json.dumps({{'checks': {checks!r}}}), encoding='utf-8')"
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "normalized.csv").write_bytes(b"id,quantity\nA,1.000\n")
    return tmp_path


def _check(root: Path, body: str) -> dict:
    return result_verify(root, {
        "command": _command(root, body),
        "targets": ["normalized.csv"],
        "report_path": "report.json",
    })


def _observe(outcome: TaskOutcome, result: dict, *, input_epoch: int = 0) -> None:
    outcome.observe("runtime_control", {"operation": "result_verify"},
                    {"ok": result["ok"], "result": result}, input_epoch=input_epoch)


def _resume_evidence(outcome: TaskOutcome, epoch: int = 0) -> RunEvidence:
    restored = TaskOutcome(json.loads(json.dumps(outcome.snapshot())))
    evidence = RunEvidence()
    for verification in restored.current_verifications(epoch):
        evidence.record_tool_result(
            tool_name="runtime_control", arguments={"operation": "result_verify"},
            execution_status="ok",
            output={"ok": verification["status"] == "passed", "result": {"verification": verification}},
            text_result="", state_changed=False,
        )
    return evidence


def test_real_result_checker_binds_report_and_exact_artifact_bytes(project: Path) -> None:
    before = file_digest(project / "normalized.csv")
    result = _check(project, "\n".join([
        "data = Path('normalized.csv').read_bytes()",
        "checks = [{'name': 'exact expected rows', 'passed': data == b'id,quantity\\nA,1.000\\n'},",
        "          {'name': 'UTF-8 without BOM', 'passed': not data.startswith(b'\\xef\\xbb\\xbf')}]",
        "Path('report.json').write_text(json.dumps({'checks': checks}), encoding='utf-8')",
    ]))
    verification = result["verification"]
    assert result["ok"] is True and result["execution_ok"] is True
    assert verification["status"] == "passed" and verification["exit_code"] == 0
    assert verification["targets"] == [{"path": str(project / "normalized.csv"), "sha256": before}]
    assert verification["report_sha256"] == file_digest(project / "report.json")
    assert len(verification["checks"]) == 2


def test_false_business_check_is_failed_despite_exit_zero(project: Path) -> None:
    result = _check(project, _report([{"name": "expected quantity", "passed": False}]))
    assert result["execution_ok"] is True
    assert result["verification"]["exit_code"] == 0
    assert result["ok"] is False
    assert result["verification"]["status"] == "failed"


def test_stdout_alone_does_not_create_a_semantic_verdict(project: Path) -> None:
    result = _check(project, "print(False)")
    assert result["execution_ok"] is True
    assert result["verification"]["status"] == "unverified"
    assert result["ok"] is False
    assert result["verification"]["checks"] == []


def test_unchanged_prior_report_is_not_reused_as_new_success(project: Path) -> None:
    original = _check(project, _report([{"name": "old check", "passed": True}]))
    assert original["ok"]
    result = _check(project, "print('no new check was performed')")
    assert result["execution_ok"] is True
    assert result["verification"]["status"] == "unverified"
    assert "fresh report" in result["error"]


@pytest.mark.parametrize("report", [
    {"checks": []},
    {"checks": [{"name": "ambiguous result", "passed": "False"}]},
    {"checks": [{"name": "numeric result", "passed": 1}]},
])
def test_checker_report_requires_nonempty_boolean_results(project: Path, report: dict) -> None:
    result = _check(project, f"Path('report.json').write_text(json.dumps({report!r}), encoding='utf-8')")
    assert result["execution_ok"] is True
    assert result["verification"]["status"] == "unverified"
    assert result["ok"] is False


def test_nonzero_checker_exit_cannot_pass_a_true_report(project: Path) -> None:
    result = _check(project, _report([{"name": "before process failure", "passed": True}]) + "\nraise SystemExit(7)")
    assert result["execution_ok"] is False
    assert result["verification"]["status"] == "failed"
    assert result["verification"]["exit_code"] == 7


def test_checker_may_not_modify_the_artifact_it_is_certifying(project: Path) -> None:
    original = file_digest(project / "normalized.csv")
    result = _check(project, "Path('normalized.csv').write_bytes(b'changed\\n')\n" +
                    _report([{"name": "claimed success", "passed": True}]))
    assert result["execution_ok"] is True
    assert result["verification"]["status"] == "unverified"
    assert result["verification"]["targets"][0]["sha256"] != original
    assert "changed during verification" in result["error"]


def test_resume_replays_latest_failure_and_rechecks_live_hashes(project: Path) -> None:
    target = str(project / "normalized.csv")
    outcome = TaskOutcome()
    _observe(outcome, _check(project, _report([{"name": "initial result", "passed": True}])))
    assert _resume_evidence(outcome).has_passing_result_verification([target])
    _observe(outcome, _check(project, _report([{"name": "recheck detects mismatch", "passed": False}])))
    assert len(outcome.verifications) == 1
    assert not _resume_evidence(outcome).has_passing_result_verification([target])
    _observe(outcome, _check(project, _report([{"name": "repaired result", "passed": True}])))
    (project / "normalized.csv").write_bytes(b"changed after the saved check\n")
    assert not _resume_evidence(outcome).has_passing_result_verification([target])


def test_missing_target_recheck_revokes_old_pass_even_if_original_bytes_return(project: Path) -> None:
    target = project / "normalized.csv"
    original = target.read_bytes()
    outcome = TaskOutcome()
    _observe(outcome, _check(project, _report([{"name": "initial result", "passed": True}])))
    failed = _check(project, "Path('normalized.csv').unlink()\n" +
                    _report([{"name": "misleading report", "passed": True}]))
    assert failed["verification"]["status"] == "unverified"
    _observe(outcome, failed)
    target.write_bytes(original)
    assert not _resume_evidence(outcome).has_passing_result_verification([str(target)])


def test_target_missing_before_check_has_bound_unverified_receipt(project: Path) -> None:
    target = project / "normalized.csv"
    target.unlink()
    result = _check(project, "Path('checker-started').write_text('yes')\n" +
                    _report([{"name": "should not run", "passed": True}]))
    assert result["ok"] is False
    assert result["verification"]["status"] == "unverified"
    assert result["verification"]["targets"] == [{"path": str(target), "sha256": ""}]
    assert not (project / "checker-started").exists()


def test_legitimate_one_off_needs_no_skill_publication(project: Path) -> None:
    decision = task_decide(project, {
        "disposition": "one_off", "reason": "Single ad hoc total for this export",
        "targets": ["normalized.csv"],
    })
    outcome = TaskOutcome()
    outcome.observe("sandbox_run", {}, {
        "ok": True, "source_path": str(project / "executed.py"), "source_sha256": "b" * 64,
    })
    assert outcome.pending()
    outcome.observe("runtime_control", {"operation": "task_decide"}, {"ok": True, "result": decision})
    _observe(outcome, _check(project, _report([{"name": "one-off result", "passed": True}])))
    restored = TaskOutcome(json.loads(json.dumps(outcome.snapshot())))
    assert restored.pending() == ""
    assert restored.decision["disposition"] == "one_off"
    assert restored.published == {} and restored.loaded == {}
    assert _resume_evidence(restored).has_passing_result_verification(restored.decision["targets"])


def test_development_cannot_finish_with_an_older_loaded_revision() -> None:
    outcome = TaskOutcome({
        "decision": {"disposition": "develop", "skill_name": "weekly-reconcile"},
        "published": {"weekly-reconcile": {"candidate_id": "new", "revision": "new-revision"}},
        "loaded": {"weekly-reconcile": {"candidate_id": "old", "revision": "old-revision"}},
    })
    assert "skill_load" in outcome.pending()


def test_reuse_keeps_valid_pinned_version_but_rejects_package_drift_after_resume(tmp_path, monkeypatch) -> None:
    from app.application.code_agent import skill_development as development, task_skills

    monkeypatch.setattr(development, "ROOT", tmp_path / "development")
    monkeypatch.setattr(task_skills, "SKILLS_ROOT", tmp_path / "installed")
    name = "pinned-reuse"

    def publish(version: int) -> dict:
        created = development.develop("skill_create", name, {})
        directory = Path(created["directory"])
        (directory / "SKILL.md").write_text(
            "---\nname: pinned-reuse\ndescription: Normalize a recurring record.\n---\n"
            "Run process.py and check the result.\n", encoding="utf-8", newline="\n",
        )
        (directory / "process.py").write_text(
            f"def transform(value):\n    return value.strip() + '-{version}'\n",
            encoding="utf-8", newline="\n",
        )
        checker = _command(directory, "import runpy\n"
                           f"assert runpy.run_path('process.py')['transform']('  item  ') == 'item-{version}'")
        config = {"candidate_id": created["candidate_id"], "command": checker}
        checked = development.develop("skill_check", name, config)
        assert checked["ok"], checked
        published = development.develop("skill_publish", name, config)
        assert published["ok"], published
        return task_skills.skill_control("skill_load", name)["skill"]

    first = publish(1)
    outcome = TaskOutcome({
        "decision": {"disposition": "reuse", "skill_name": name}, "loaded": {name: first},
    })
    second = publish(2)
    assert second["candidate_id"] != first["candidate_id"]
    assert development.active_package(name)["candidate_id"] == second["candidate_id"]
    assert outcome.pending() == ""
    saved = json.loads(json.dumps(outcome.snapshot()))
    assert TaskOutcome(saved).pending() == ""

    first_directory = Path(first["directory"])
    added = first_directory / "late-check.py"
    added.write_text("assert True\n", encoding="utf-8", newline="\n")
    assert "требует исправления" in outcome.pending()
    assert "требует исправления" in TaskOutcome(saved).pending()
    added.unlink()
    assert outcome.pending() == ""
    (first_directory / "process.py").write_text("def transform(value):\n    return ''\n",
                                                 encoding="utf-8", newline="\n")
    assert "требует исправления" in outcome.pending()
    assert "требует исправления" in TaskOutcome(saved).pending()


def test_task_version_detects_content_change_even_when_size_and_mtime_match(project: Path) -> None:
    target = project / "normalized.csv"
    outcome = TaskOutcome({"decision": {"disposition": "one_off", "targets": [str(target)]}})
    before = outcome.version(0)
    stat = target.stat()
    target.write_bytes(target.read_bytes().replace(b"1.000", b"9.000"))
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert target.stat().st_size == stat.st_size
    assert target.stat().st_mtime_ns == stat.st_mtime_ns
    assert outcome.version(0) != before


def test_successful_code_writes_need_absolute_receipt_or_known_project_root(project: Path) -> None:
    outcome = TaskOutcome()
    csv_path = project / "normalized.csv"
    code_path = project / "transform.py"
    code_path.write_bytes(b"print('initial')\n")
    outcome.observe("write_file", {}, {"ok": True, "touched_path": str(csv_path)})
    outcome.observe("write_file", {}, {"ok": False, "touched_path": str(code_path)})
    outcome.observe("edit_file", {}, {"ok": True, "touched_path": code_path.name})
    assert outcome.sources == {}
    assert outcome.pending() == ""

    outcome.observe("write_file", {}, {"ok": True, "touched_path": str(code_path)})
    first_hash = file_digest(code_path)
    assert outcome.sources == {str(code_path): first_hash}
    assert "runtime_control(task_decide)" in outcome.pending()
    code_path.write_bytes(b"print('updated')\n")
    outcome.observe("edit_file", {}, {"ok": True, "touched_path": str(code_path)})
    assert outcome.sources[str(code_path)] == file_digest(code_path)
    assert outcome.sources[str(code_path)] != first_hash
    restored = TaskOutcome(json.loads(json.dumps(outcome.snapshot())))
    assert restored.sources == outcome.sources
    relative = TaskOutcome()
    relative.observe("write_file", {}, {"ok": True, "touched_path": code_path.name}, project_root=project)
    assert relative.sources == {str(code_path): file_digest(code_path)}


def test_resume_invalidates_unchanged_outputs_after_input_or_code_change_without_reviving_old_pass(project: Path) -> None:
    source = project / "supplier.csv"
    source.write_bytes(b"week1\n")
    second = project / "summary.csv"
    second.write_bytes(b"total\n1.000\n")
    targets = [str(project / "normalized.csv"), str(second)]
    outcome = TaskOutcome({"decision": {
        "disposition": "one_off", "inputs": [str(source)], "targets": targets,
    }})
    result = result_verify(project, {
        "command": _command(project, _report([{"name": "both outputs match this input", "passed": True}])),
        "targets": targets, "report_path": "report.json",
    })
    _observe(outcome, result, input_epoch=5)
    restored = TaskOutcome(json.loads(json.dumps(outcome.snapshot())))
    output_hashes = {path: file_digest(Path(path)) for path in targets}
    assert restored.checks_current(targets, 5)
    assert restored.unverified_targets(targets, 5) == []
    assert all(item["status"] == "passed" for item in json.loads(restored.context(5).split("\n", 1)[1])["checks"])
    assert _resume_evidence(restored, 5).has_passing_result_verification(targets)

    source.write_bytes(b"week2\n")
    assert output_hashes == {path: file_digest(Path(path)) for path in targets}
    assert not restored.checks_current(targets, 5)
    assert restored.unverified_targets(targets, 5) == targets
    assert all(item["status"] == "unverified" for item in restored.current_verifications(5))
    assert all(item["status"] == "unverified" for item in json.loads(restored.context(5).split("\n", 1)[1])["checks"])
    assert not _resume_evidence(restored, 5).has_passing_result_verification(targets)

    source.write_bytes(b"week1\n")
    assert not restored.checks_current(targets, 6)
    assert all(item["status"] == "unverified" for item in json.loads(restored.context(6).split("\n", 1)[1])["checks"])
    assert not _resume_evidence(restored, 6).has_passing_result_verification(targets)
    # The fresh failed check overlaps only one target of the older two-file pass.
    failed = _check(project, _report([{"name": "new checker detects mismatch", "passed": False}]))
    _observe(restored, failed, input_epoch=6)
    stopped_again = TaskOutcome(json.loads(json.dumps(restored.snapshot())))
    assert len(stopped_again.verifications) == 2
    assert not stopped_again.checks_current(targets, 6)
    assert stopped_again.unverified_targets(targets, 6) == targets
    replayed = _resume_evidence(stopped_again, 6)
    assert not replayed.has_passing_result_verification([targets[0]])
    assert not replayed.has_passing_result_verification([targets[1]])
    assert output_hashes == {path: file_digest(Path(path)) for path in targets}


def test_learning_receipt_follows_selected_skill_version_and_live_report(project: Path) -> None:
    target = str(project / "normalized.csv")
    outcome = TaskOutcome({"decision": {
        "disposition": "reuse", "skill_name": "reconciliation", "targets": [target], "inputs": [],
    }, "loaded": {"reconciliation": {"sha256": "a" * 64, "revision": "v1"}}})
    _observe(outcome, _check(project, _report([{"name": "exact result", "passed": True}])))
    restored = TaskOutcome(json.loads(json.dumps(outcome.snapshot())))
    evidence = restored.learning_evidence(0)
    assert evidence is not None
    assert evidence["skill_binding"]["identity"] == {"sha256": "a" * 64, "revision": "v1"}
    restored.loaded["unrelated"] = {"sha256": "c" * 64}
    assert restored.learning_evidence(0) == evidence
    restored.loaded["reconciliation"] = {"sha256": "b" * 64, "revision": "v2"}
    assert not restored.checks_current([target], 0)
    assert restored.learning_evidence(0) is None
    _observe(restored, _check(project, _report([{"name": "new version result", "passed": True}])))
    assert restored.learning_evidence(0)["skill_binding"]["identity"]["revision"] == "v2"
    (project / "report.json").write_text('{"checks": []}', encoding="utf-8")
    assert restored.learning_evidence(0) is None
    assert TaskOutcome({"decision": outcome.decision, "loaded": outcome.loaded}).learning_evidence(0) is None


def test_uri_file_fields_fail_before_execution_and_preserve_decision(project: Path, monkeypatch) -> None:
    from app.application.code_agent.tools import _run
    from app.application.code_agent.tools._runtime_control import tool_runtime_control

    def unexpected_execution(*args, **kwargs):
        pytest.fail("Invalid file fields must be rejected before checker execution")

    monkeypatch.setattr(_run, "tool_run_bash", unexpected_execution)
    config = {"disposition": "one_off", "reason": "Ad hoc result", "targets": ["normalized.csv"]}
    outcome = TaskOutcome({"decision": task_decide(project, config)["task_decision"]})
    saved = outcome.snapshot()
    for field, uri in (("inputs", "http://127.0.0.1:63246/docs"),
                       ("inputs", "HTTPS://example.test/docs"),
                       ("targets", "file:///tmp/result.csv")):
        result = tool_runtime_control(project, operation="task_decide", config={**config, field: [uri]})
        assert result["ok"] is False and result["status"] == "failed"
        assert f"config.{field}" in result["error"]["message"]
        assert "local file" in result["error"]["message"]
        assert "SOURCES.md" in result["error"]["message"]
        outcome.observe("runtime_control", {"operation": "task_decide"}, result)
        assert outcome.snapshot() == saved
    invalid_inputs = tool_runtime_control(project, operation="task_decide", config={**config, "inputs": "docs.json"})
    assert "config.inputs" in invalid_inputs["error"]["message"]

    check = {"command": "unused", "targets": ["normalized.csv"], "report_path": "report.json"}
    for field, value in (("targets", ["s3://bucket/result.csv"]),
                         ("report_path", "https://example.test/report.json")):
        result = tool_runtime_control(project, operation="result_verify", config={**check, field: value})
        assert result["ok"] is False and result["status"] == "failed"
        assert f"config.{field}" in result["error"]["message"]


def test_local_input_snapshot_can_be_bound_and_invalidates_learning_on_change(project: Path) -> None:
    from app.application.code_agent.tools._runtime_control import tool_runtime_control

    source = project / "docs-snapshot.json"
    source.write_bytes(b'{"schema_version": 1}\n')
    result = tool_runtime_control(project, operation="task_decide", config={
        "disposition": "reuse", "reason": "Use a saved export capability", "skill_name": "export",
        "inputs": [source.name], "targets": ["normalized.csv"],
    })
    assert result["ok"] is True
    outcome = TaskOutcome({"loaded": {"export": {"sha256": "a" * 64}}})
    outcome.observe("runtime_control", {"operation": "task_decide"}, result)
    _observe(outcome, _check(project, _report([{"name": "expected exported rows", "passed": True}])))
    evidence = outcome.learning_evidence(0)
    assert evidence is not None
    assert evidence["inputs"] == [{"path": str(source), "sha256": file_digest(source)}]
    source.write_bytes(b'{"schema_version": 2}\n')
    assert outcome.learning_evidence(0) is None


def test_file_guard_preserves_native_path_forms_and_future_targets(project: Path, monkeypatch) -> None:
    values = ["output/future.csv", "output/with spaces.csv", "report:2026.csv", "./https://archive/file.csv",
              r"C:\work\input.csv", "C:/work/input.csv", "C://work/input.csv", r"\\server\share\input.csv",
              "/var/tmp/input.csv"]
    # No drive/share access: this checks lexical acceptance, not filesystem availability.
    with monkeypatch.context() as lexical:
        lexical.setattr(Path, "resolve", lambda self: self)
        decision = task_decide(project, {"disposition": "one_off", "reason": "Native path compatibility",
                                         "inputs": values, "targets": []})["task_decision"]
        expected = [str(Path(value) if Path(value).is_absolute() else project / value) for value in values]
        assert decision["inputs"] == list(dict.fromkeys(expected))
    target = project / "not-created-yet.csv"
    decision = task_decide(project, {"disposition": "one_off", "reason": "Declare a future result",
                                     "targets": [target.name]})["task_decision"]
    assert decision["targets"] == [str(target)]
    assert not target.exists()
