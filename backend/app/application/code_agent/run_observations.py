"""Run-local evidence and result accounting; ordering remains coordinator-owned."""
from __future__ import annotations

from typing import Any
from pathlib import Path

from app.application.code_agent.command_progress import CommandProgress
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.task_outcomes import TaskOutcome
from app.application.code_agent.taskspec import CriteriaTracker

def _record_criterion_verdict(criteria, name: str, args: dict, tool_meta: dict,
                              text_result: str, tool_ok: bool, auto: bool = False) -> bool:
    """Feed one executed tool call into the observational criterion tracker.

    A verifier tool records structured evidence; ``run_bash`` records real
    stdout/stderr and exit code. The tracker never forces another model turn.
    ``auto`` remains only for compatibility with persisted historical reports.
    """
    if not criteria.items:
        return False
    if tool_meta.get("verifier"):
        return criteria.record(tool_name=name, args=args, ok=tool_ok,
                               evidence=str(tool_meta.get("evidence") or ""),
                               meta=tool_meta, auto=auto)
    if name == "runtime_control" and args.get("operation") == "result_verify":
        return criteria.record(tool_name=name, args=args, ok=tool_ok,
                               evidence=text_result, meta=tool_meta, auto=auto)
    if name == "run_bash":
        return criteria.record(tool_name=name, args=args, ok=tool_ok,
                               evidence=text_result, meta=tool_meta, auto=auto)
    return False


class RunObservations:
    """Own each existing ledger once, with separate pre/post receipt stages."""

    def __init__(self, *, task_spec, durable_state: dict[str, Any], resume: bool,
                 initial_sources: list[dict[str, Any]] | None = None):
        self.evidence = RunEvidence(sources=initial_sources or [])
        self.criteria = CriteriaTracker.from_spec(task_spec)
        self.outcome = TaskOutcome(durable_state.get("task_outcome"))
        self.commands = CommandProgress.from_snapshot(durable_state.get("command_progress"))
        self.code_input_epoch = int(durable_state.get("code_input_epoch") or 0)
        self.mutated_files = list(durable_state.get("mutated_files") or [])
        self.verifications = list(durable_state.get("verifications") or [])
        self.failed_attempts = list(durable_state.get("failed_attempts") or [])
        for verification in self.outcome.current_verifications(self.code_input_epoch):
            self.evidence.record_tool_result(
                tool_name="runtime_control", arguments={"operation": "result_verify"},
                execution_status="ok", output={"ok": verification.get("status") == "passed",
                    "operation": "result_verify", "result": {"verification": verification}},
                text_result="", state_changed=False,
            )
        project_epoch = int(durable_state.get("project_epoch") or 0)
        criteria_epoch = int(durable_state.get("criteria_epoch") or 0)
        if resume and criteria_epoch == project_epoch:
            self.criteria.restore_report(list(durable_state.get("criteria") or []))

    def capture_verification(self, name: str, args: dict) -> dict | None:
        return (self.outcome.verification_context(self.code_input_epoch)
                if name == "runtime_control" and args.get("operation") == "result_verify" else None)

    def bind_verification(self, output: dict, before: dict | None) -> dict:
        if before is None:
            return output
        return self.outcome.bind_verification(output, before, self.code_input_epoch)

    def before_dispatch(self, name: str, args: dict, *, root: Path) -> dict | None:
        return self.commands.before_dispatch(
            name, args, epoch=self.outcome.version(self.code_input_epoch), cwd=str(root),
        )

    def observe_result(self, *, name: str, args: dict, output: dict, status: str,
                       text: str, state_changed: bool, root: Path, bom_selected: bool) -> dict:
        self.evidence.record_tool_result(
            tool_name=name, arguments=args, execution_status=status,
            output=output, text_result=text, state_changed=state_changed,
        )
        if state_changed:
            self.code_input_epoch += 1
        self.outcome.observe(name, args, output, project_root=root,
                             input_epoch=self.code_input_epoch, execution_status=status)
        recovery = self.commands.observe(
            name, args, output, execution_status=status,
            epoch=self.outcome.version(self.code_input_epoch), cwd=str(root),
        )
        fields = {"task_outcome": self.outcome.snapshot(),
                  "command_progress": self.commands.snapshot(),
                  "code_input_epoch": self.code_input_epoch,
                  "bom_validation_selected": bom_selected}
        if recovery:
            fields["recovery_context"] = recovery
        if state_changed:
            self.criteria.invalidate_after_mutation()
            self.verifications.clear()
        return fields

    def complete_result(self, *, name: str, args: dict, output: dict, status: str,
                        text: str, ok: bool, state_changed: bool, verification: str) -> None:
        if output.get("touched_path") and state_changed:
            mutated = str(output.get("touched_path"))
            if mutated not in self.mutated_files:
                self.mutated_files.append(mutated)
        if verification:
            self.verifications.append(verification)
        executed_result_check = (
            name == "runtime_control" and args.get("operation") == "result_verify"
            and status in {"error", "cancelled"}
            and isinstance(output.get("result"), dict)
            and isinstance(output["result"].get("verification"), dict)
        )
        if status == "ok" or executed_result_check:
            _record_criterion_verdict(self.criteria, name, args, output, text, ok)
