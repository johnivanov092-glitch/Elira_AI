"""Executed SSH checks, lifecycle attribution and recovery across executor errors."""
from __future__ import annotations

import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.code_agent.run_observations import RunObservations
from app.application.code_agent.taskspec import (
    CriteriaTracker, TaskSpec, merge_task_spec, requirement_id, task_spec_from_report, taskspec_report,
)
from app.application.tool_providers import ssh_provider as ssh


def process(stdout: str = "", *, code: int = 0, stderr: str = ""):
    return CompletedProcess(["ssh"], code, stdout.encode("utf-8"), stderr.encode("utf-8"))


@pytest.mark.parametrize("tool,args", [
    (ssh.tool_ssh_exists, {"path": "/tmp/proof"}),
    (ssh.tool_ssh_not_exists, {"path": "/tmp/proof"}),
    (ssh.tool_ssh_port_check, {"port": 8123}),
])
@pytest.mark.parametrize("result", [
    process("MISSING", code=255, stderr="Permission denied (publickey)"),
    process("NOT-LISTENING", code=1, stderr="command failed"),
    process(""), process("UNKNOWN"), process("MISSING\nEXISTS FILE"),
])
def test_transport_command_and_unknown_results_are_not_verdicts(monkeypatch, tool, args, result):
    monkeypatch.setattr(ssh, "run_registered_process", lambda *a, **kw: result)
    output = tool(host="proof-host", **args)
    assert output["ok"] is False
    assert "verifier" not in output
    assert "ssh_verification" not in output


@pytest.mark.parametrize("tool,args", [
    (ssh.tool_ssh_exists, {"path": "/tmp/proof"}),
    (ssh.tool_ssh_port_check, {"port": 8123}),
])
def test_probe_exception_is_diagnostic(monkeypatch, tool, args):
    def interrupted(*a, **kw):
        raise TimeoutError("interrupted probe")
    monkeypatch.setattr(ssh, "run_registered_process", interrupted)
    output = tool(host="proof-host", **args)
    assert output["ok"] is False
    assert "verifier" not in output


def test_posix_fallback_requires_success_and_recognised_negative(monkeypatch):
    outputs = iter([process(code=127, stderr="sh: powershell: not found"), process("MISSING")])
    monkeypatch.setattr(ssh, "run_registered_process", lambda *a, **kw: next(outputs))
    output = ssh.tool_ssh_exists(host="proof-host", path="/tmp/proof")
    assert output["ok"] is False
    assert output["verifier"] is True
    assert output["ssh_verification"]["observed"] is False


def test_closed_port_is_a_real_negative_only_after_socket_inspection(monkeypatch):
    outputs = iter([process(code=127, stderr="sh: powershell: not found"), process("NOT-LISTENING")])
    calls = []
    def run(argv):
        calls.append(argv)
        return next(outputs)
    monkeypatch.setattr(ssh, "run_registered_process", run)
    output = ssh.tool_ssh_port_check(host="proof-host", port=8123)
    assert output["verifier"] is True
    assert output["ok"] is False
    assert "output=$(ss -ltn) || exit $?" in calls[-1][-1]


def observations(criteria: list[str], contracts: dict | None = None) -> RunObservations:
    return RunObservations(task_spec=TaskSpec(success_criteria=criteria, criterion_contracts=contracts or {}),
                           durable_state={}, resume=False)


def feed(obs: RunObservations, tool: str, args: dict, output: dict, status: str):
    obs.complete_result(name=tool, args=args, output=output, status=status, text=output.get("text", ""),
                        ok=output["ok"], state_changed=False, verification="")


def test_executed_negative_crosses_executor_error_and_can_recover(monkeypatch):
    criterion = "файл `/tmp/proof` должен существовать сейчас"
    obs = observations([criterion], {criterion: {"host": "proof-host"}})
    args = {"host": "proof-host", "path": "/tmp/proof"}
    for sentinel, status, expected in [("EXISTS FILE", "ok", "confirmed"),
                                       ("MISSING", "error", "failed"),
                                       ("EXISTS FILE", "ok", "confirmed")]:
        monkeypatch.setattr(ssh, "run_registered_process", lambda *a, **kw: process(sentinel))
        feed(obs, "ssh_exists", args, ssh.tool_ssh_exists(**args), status)
        assert obs.criteria.items[0]["status"] == expected


def test_arbitrary_error_and_cancelled_receipts_cannot_change_criteria(monkeypatch):
    obs = observations(["файл `/tmp/proof` существует"])
    args = {"host": "proof-host", "path": "/tmp/proof"}
    monkeypatch.setattr(ssh, "run_registered_process", lambda *a, **kw: process("MISSING"))
    negative = ssh.tool_ssh_exists(**args)
    feed(obs, "ssh_exists", args, negative, "cancelled")
    feed(obs, "ssh_exists", args, {"ok": False, "verifier": True, "evidence": "MISSING"}, "error")
    feed(obs, "ssh_exists", args, {"ok": True, "verifier": True, "evidence": "EXISTS FILE"}, "ok")
    feed(obs, "run_bash", {"command": "pytest"}, {"ok": False, "exit_code": 1}, "error")
    assert obs.criteria.items[0]["status"] == "unconfirmed"


def test_historical_setup_survives_cleanup_and_current_requirement_fails(monkeypatch):
    old = "файл `/tmp/proof` создан при setup"
    current = "файл `/tmp/proof` должен существовать сейчас"
    obs = observations([old, current])
    args = {"host": "proof-host", "path": "/tmp/proof"}
    monkeypatch.setattr(ssh, "run_registered_process", lambda *a, **kw: process("EXISTS FILE"))
    feed(obs, "ssh_exists", args, ssh.tool_ssh_exists(**args), "ok")
    obs.criteria.invalidate_after_mutation()
    assert obs.criteria.items[0]["status"] == "confirmed"
    monkeypatch.setattr(ssh, "run_registered_process", lambda *a, **kw: process("MISSING"))
    feed(obs, "ssh_not_exists", args, ssh.tool_ssh_not_exists(**args), "ok")
    assert [item["status"] for item in obs.criteria.items] == ["confirmed", "failed"]


def test_literal_condition_cannot_confirm_from_a_prefix_or_changed_case(monkeypatch):
    obs = observations(["файл `/tmp/proof` содержит строку `Status=OK-more`"])
    monkeypatch.setattr(ssh, "_read_remote_bytes", lambda *a, **kw: (b"Status=OK-more", None))
    for pattern in ("Status=OK", "status=ok-more"):
        args = {"host": "proof-host", "path": "/tmp/proof", "pattern": pattern}
        output = ssh.tool_ssh_assert_contains(**args)
        feed(obs, "ssh_assert_contains", args, output, "ok" if output["ok"] else "error")
    assert obs.criteria.items[0]["status"] == "unconfirmed"


def test_host_full_path_and_condition_attribution(monkeypatch):
    criterion = "файл `/tmp/a/proof.txt` на хосте proof-host содержит строку `ok=yes`"
    obs = observations([criterion])
    monkeypatch.setattr(ssh, "_read_remote_bytes", lambda *a, **kw: (b"ok=yes", None))
    for args in [{"host": "other-host", "path": "/tmp/a/proof.txt", "pattern": "ok=yes"},
                 {"host": "proof-host", "path": "/tmp/b/proof.txt", "pattern": "ok=yes"},
                 {"host": "proof-host", "path": "/tmp/a/proof.txt", "pattern": "ok=no"}]:
        output = ssh.tool_ssh_assert_contains(**args)
        feed(obs, "ssh_assert_contains", args, output, "ok" if output["ok"] else "error")
    assert obs.criteria.items[0]["status"] == "unconfirmed"
    args = {"host": "proof-host", "path": "/tmp/a/proof.txt", "pattern": "ok=yes"}
    feed(obs, "ssh_assert_contains", args, ssh.tool_ssh_assert_contains(**args), "ok")
    assert obs.criteria.items[0]["status"] == "confirmed"


def test_resume_retains_host_object_binding(monkeypatch):
    criterion = "файл `proof.txt` существует"
    obs = observations([criterion])
    monkeypatch.setattr(ssh, "run_registered_process", lambda *a, **kw: process("EXISTS FILE"))
    args = {"host": "proof-host", "path": "/tmp/a/proof.txt"}
    feed(obs, "ssh_exists", args, ssh.tool_ssh_exists(**args), "ok")
    restored = CriteriaTracker.from_spec(obs.task_spec)
    restored.restore_report(obs.criteria.report())
    monkeypatch.setattr(ssh, "run_registered_process", lambda *a, **kw: process("MISSING"))
    wrong = {"host": "proof-host", "path": "/tmp/b/proof.txt"}
    output = ssh.tool_ssh_exists(**wrong)
    assert restored.record(tool_name="ssh_exists", args=wrong, ok=False,
                           evidence=output["evidence"], meta=output) is False
    assert restored.items[0]["status"] == "confirmed"


def test_clarification_preserves_goal_requirements_and_replaces_only_explicit_id():
    spec = TaskSpec(goal="исходная цель", success_criteria=["русский язык", "суммы правильные"])
    amended = merge_task_spec(spec, "Критерии готовности:\n- вложенные скидки проверены")
    assert amended.goal == "исходная цель"
    assert amended.success_criteria == ["русский язык", "суммы правильные", "вложенные скидки проверены"]
    identity = requirement_id("суммы правильные")
    replaced = merge_task_spec(amended, f"замени критерий [{identity}]: суммы с точностью 0.001")
    assert replaced.success_criteria == ["русский язык", "суммы с точностью 0.001", "вложенные скидки проверены"]
    assert replaced.criterion_contracts["суммы с точностью 0.001"]["requirement_id"] == identity


def test_durable_report_preserves_all_requirements_and_rejects_bad_types():
    spec = TaskSpec(goal="цель " * 100, success_criteria=[f"требование {i}" for i in range(15)],
                    constraints=["CSV не изменять"], details=["вложенные поля"],
                    criterion_contracts={"требование 0": {"lifecycle": "historical", "host": "h"}})
    report = taskspec_report(spec)
    assert task_spec_from_report(report) == spec
    assert len(report["success_criteria"]) == 15
    assert task_spec_from_report({**report, "success_criteria": [1]}) is None


def test_resume_without_spec_preserves_runtime_declared_requirements():
    saved = {"task_outcome": {"contract": {"goal": "initial", "revision": 4,
             "requirements": [{"id": "declared", "text": "русский язык", "mandatory": True}]}}}
    obs = RunObservations(task_spec=None, durable_state=saved, resume=True)
    assert obs.outcome.contract == saved["task_outcome"]["contract"]
