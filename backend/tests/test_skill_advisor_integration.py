"""One public-loop proof of learning eligibility, durable advice and opt-out."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from _runtime_roles import runtime_text
from app.application.code_agent import skill_advisor, task_skills
from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.task_outcomes import TaskOutcome


def command(path: Path) -> str:
    arguments = [sys.executable, str(path)]
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


@pytest.mark.parametrize("mutate_input", [False, True])
def test_public_loop_learns_only_bound_checked_results_and_keeps_full_catalog(tmp_path, monkeypatch, mutate_input):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ELIRA_SKILL_ADVISOR_MODE", "shadow")
    monkeypatch.setattr(skill_advisor, "ROOT", tmp_path / "advisor")
    source = tmp_path / "input.txt"
    source.write_text("7\n", encoding="utf-8")
    output = tmp_path / "result.txt"
    report = tmp_path / "checks.json"
    processor = tmp_path / "process.py"
    processor.write_text(
        "from pathlib import Path\n"
        "Path('result.txt').write_bytes((str(int(Path('input.txt').read_text()) * 2) + '\\n').encode('utf-8'))\n",
        encoding="utf-8",
    )
    checker = tmp_path / "check.py"
    checker.write_text(
        "import json\nfrom pathlib import Path\n"
        "passed = int(Path('result.txt').read_text()) == int(Path('input.txt').read_text()) * 2\n"
        + ("Path('input.txt').write_bytes(b'8\\n')\n" if mutate_input else "") +
        "Path('checks.json').write_text(json.dumps({'checks': [{'name': 'expected doubled value', 'requirement_id': 'req-double', 'passed': passed}]}))\n"
        "raise SystemExit(0 if passed else 1)\n",
        encoding="utf-8",
    )
    sequence = [
        ("runtime_control", {"operation": "skill_load", "name": "python"}),
        ("runtime_control", {"operation": "task_decide", "config": {
            "disposition": "reuse", "reason": "Existing Python procedure", "skill_name": "python",
            "inputs": [str(source)], "targets": [str(output)],
            "requirements": [{"id": "req-double", "text": "Удвоенное значение соответствует исходному числу", "mandatory": True}],
        }}),
        ("run_bash", {"command": command(processor)}),
        ("runtime_control", {"operation": "result_verify", "config": {
            "command": command(checker), "targets": [str(output)], "report_path": str(report),
        }}),
    ]
    captured = []

    def chat(**kwargs):
        captured.append(kwargs["messages"])
        index = len(captured) - 1
        if index >= len(sequence):
            return {"message": {"content": "Готово, результат проверен.", "tool_calls": []}}
        tool, arguments = sequence[index]
        return {"message": {"content": "", "tool_calls": [{"id": f"step-{index}",
            "function": {"name": tool, "arguments": arguments}}]}}

    events = list(stream_code_agent(
        user_message="Удвой целое число из input.txt скриптом Python и проверь результат.",
        project_root=tmp_path, run_id="verified-advisor", chat_fn=chat, auto_remember=True,
        permission_mode="bypass", num_ctx=32768,
    ))
    assert output.read_bytes() == b"14\n"
    state = RunJournal.load("verified-advisor").state
    assert state["skill_advisor"]["mode"] == "shadow"
    if mutate_input:
        assert events[-1]["answer_status"] == "degraded", events[-1]
        assert state["skill_advisor_learning"]["status"] == "ineligible"
        assert not (skill_advisor.ROOT / "dataset.json").exists()
        receipt = state["task_outcome"]["verifications"][-1]
        assert receipt["exit_code"] == 0 and receipt["checks"][0]["passed"] is True
        assert receipt["status"] == "unverified"
        check_event = next(event for event in events if event.get("type") == "tool_call"
                           and event.get("arguments", {}).get("operation") == "result_verify")
        assert check_event["ok"] is False
        assert "verification_inputs_changed" in check_event["result"]
        assert "Recompute the results" in str(captured[-1])
        restored = TaskOutcome(json.loads(json.dumps(state["task_outcome"])))
        epoch = int(state.get("code_input_epoch") or 0)
        assert not restored.checks_current([str(output)], epoch)
        assert restored.learning_evidence(epoch) is None
        evidence = RunEvidence()
        for verification in restored.current_verifications(epoch):
            evidence.record_tool_result(
                tool_name="runtime_control", arguments={"operation": "result_verify"},
                execution_status="ok", output={"ok": verification["status"] == "passed",
                "result": {"verification": verification}}, text_result="", state_changed=False,
            )
        assert not evidence.has_passing_result_verification([str(output)])
        # Restoring the old input cannot turn this rejected check into a pass.
        source.write_bytes(b"7\n")
        assert not restored.checks_current([str(output)], epoch)
        assert restored.learning_evidence(epoch) is None
        return
    assert events[-1]["ok"] is True and events[-1]["answer_status"] == "complete", events[-1]
    assert state["skill_advisor_learning"]["status"] == "recorded", state["skill_advisor_learning"]
    sample = json.loads((skill_advisor.ROOT / "dataset.json").read_text())["samples"][0]
    assert sample["binding"]["name"] == "python"
    assert sample["binding"]["identity"]["sha256"] == state["active_skills"][0]["sha256"]
    assert task_skills.learn_from_run("verified-advisor", state)["status"] == "duplicate"
    truncated = {**state, "request": {**state["request"], "user_message": "Запрос\n[... truncated]"}}
    assert task_skills.learn_from_run("truncated", truncated) == {
        "status": "ineligible", "reason": "truncated_request",
    }
    assert "[Подсказка по проверенному опыту" not in str(captured)
    assert all(item["name"] in str(captured[0]) for item in task_skills.discover_skills()["skills"])

    report.write_text('{"checks": []}', encoding="utf-8")
    assert task_skills.learn_from_run("changed-report", state)["status"] == "ineligible"
    assert len(json.loads((skill_advisor.ROOT / "dataset.json").read_text())["samples"]) == 1
    monkeypatch.setenv("ELIRA_SKILL_ADVISOR_MODE", "off")
    assert task_skills.advisor_context("Python")[1]["status"] == "disabled"
    assert task_skills.learn_from_run("verified-advisor", state)["status"] == "disabled"


@pytest.mark.parametrize("recovery", ["still_pending", "correct_decision", "verify_additional"])
def test_missing_result_feedback_requires_an_explicit_model_repair(tmp_path, monkeypatch, recovery):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ELIRA_SKILL_ADVISOR_MODE", "shadow")
    monkeypatch.setattr(skill_advisor, "ROOT", tmp_path / "advisor")
    source = tmp_path / "input.txt"
    source.write_bytes(b"7\n")
    outputs = [tmp_path / "double.txt", tmp_path / "triple.txt"]
    for path, value in zip(outputs, (14, 21)):
        path.write_text(str(value), encoding="utf-8")
    report = tmp_path / "checks.json"
    checker = tmp_path / "check.py"
    checker.write_text(
        "import json\nfrom pathlib import Path\n"
        "number = int(Path('input.txt').read_text())\n"
        "checks = [{'name': name, 'requirement_id': identifier, 'passed': int(Path(name).read_text()) == number * factor}\n"
        "          for name, factor, identifier in [('double.txt', 2, 'req-double'), ('triple.txt', 3, 'req-triple')]]\n"
        "Path('checks.json').write_text(json.dumps({'checks': checks}), encoding='utf-8')\n"
        "raise SystemExit(0 if all(row['passed'] for row in checks) else 1)\n",
        encoding="utf-8", newline="\n",
    )
    additional_checker = tmp_path / "check_report.py"
    additional_checker.write_text(
        "import json\nfrom pathlib import Path\n"
        "rows = json.loads(Path('checks.json').read_text(encoding='utf-8'))['checks']\n"
        "passed = rows == [{'name': 'double.txt', 'requirement_id': 'req-double', 'passed': True}, {'name': 'triple.txt', 'requirement_id': 'req-triple', 'passed': True}]\n"
        "Path('report_receipt.json').write_text(json.dumps({'checks': [{'name': 'report content', 'requirement_id': 'req-report', 'passed': passed}]}), encoding='utf-8')\n"
        "raise SystemExit(0 if passed else 1)\n",
        encoding="utf-8", newline="\n",
    )
    declared_targets = [str(path) for path in [*outputs, report]]
    decision = {"disposition": "reuse", "reason": "Use the saved Python procedure", "skill_name": "python",
                "inputs": [str(source)], "targets": declared_targets, "requirements": [
                    {"id": "req-double", "text": "double.txt содержит удвоенное исходное число", "mandatory": True},
                    {"id": "req-triple", "text": "triple.txt содержит утроенное исходное число", "mandatory": True},
                    *([{"id": "req-report", "text": "checks.json содержит верные результаты проверок", "mandatory": True}]
                      if recovery != "correct_decision" else []),
                ]}
    verify = {"operation": "result_verify", "config": {
        "command": command(checker), "targets": [str(path) for path in outputs], "report_path": str(report),
    }}
    sequence = [
        ("runtime_control", {"operation": "skill_load", "name": "python"}),
        ("run_bash", {"command": command(checker)}),
        ("runtime_control", {"operation": "task_decide", "config": decision}),
        ("runtime_control", verify),
        None,  # Premature final response must receive one actionable correction.
    ]
    if recovery == "correct_decision":
        sequence += [
            ("runtime_control", {"operation": "task_decide", "config": {
                **decision, "targets": [str(path) for path in outputs],
            }}),
            ("runtime_control", verify),
        ]
    elif recovery == "verify_additional":
        sequence.append(("runtime_control", {"operation": "result_verify", "config": {
            "command": command(additional_checker), "targets": [str(report)],
            "report_path": str(tmp_path / "report_receipt.json"),
        }}))
    else:
        sequence.append(("runtime_control", verify))
    captured = []

    def chat(**kwargs):
        captured.append(kwargs["messages"])
        index = len(captured) - 1
        action = sequence[index] if index < len(sequence) else None
        if action is None:
            return {"message": {"content": "Готово, результаты проверены.", "tool_calls": []}}
        tool, arguments = action
        return {"message": {"content": "", "tool_calls": [{"id": f"step-{index}",
            "function": {"name": tool, "arguments": arguments}}]}}

    events = list(stream_code_agent(
        user_message="Удвой и утрой число из input.txt скриптом Python и проверь результаты."
        + (" Сохрани также проверочный JSON как результат задачи." if recovery != "correct_decision" else ""),
        project_root=tmp_path, run_id="result-feedback", chat_fn=chat, auto_remember=True,
        permission_mode="bypass", num_ctx=32768,
    ))
    corrections = [event for event in events if event.get("type") == "task_outcome_changed"]
    assert len(corrections) == 1
    correction = corrections[0]["task_outcome"]["correction"]
    assert report.name in correction and all(path.name not in correction for path in outputs)
    if recovery == "correct_decision":
        assert "task_decide.config.targets" in correction and "result_verify.config.targets" in correction
        assert "проверь его отдельно" in correction
    else:
        assert "req-report" in correction and "req-double" not in correction and "req-triple" not in correction
        assert "Не подтверждены обязательные требования" in correction and "проверь" in correction.casefold()
    assert correction in runtime_text(captured[5])
    assert corrections[0]["task_outcome"]["decision"]["targets"] == declared_targets
    state = RunJournal.load("result-feedback").state
    restored = TaskOutcome(json.loads(json.dumps(state["task_outcome"])))
    targets = [str(path) for path in outputs] if recovery == "correct_decision" else declared_targets
    assert restored.decision["targets"] == targets
    epoch = int(state.get("code_input_epoch") or 0)
    pending = recovery == "still_pending"
    assert restored.unverified_targets(targets, epoch) == ([str(report)] if pending else [])
    assert restored.checks_current(targets, epoch) is not pending
    assert (restored.learning_evidence(epoch) is None) is pending
    assert events[-1]["answer_status"] == ("degraded" if pending else "complete"), events[-1]
    assert state["skill_advisor_learning"]["status"] == ("ineligible" if pending else "recorded")
