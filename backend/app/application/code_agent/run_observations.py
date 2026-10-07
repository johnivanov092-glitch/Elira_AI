"""Run-local evidence and result accounting; ordering remains coordinator-owned."""
from __future__ import annotations

from typing import Any
from pathlib import Path

from app.application.code_agent.command_progress import CommandProgress
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.task_outcomes import TaskOutcome
from app.application.code_agent.taskspec import (
    CriteriaTracker, executed_ssh_verification, merge_task_spec, task_requirements,
)

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
    if name == "run_bash":
        return criteria.record(tool_name=name, args=args, ok=tool_ok,
                               evidence=text_result, meta=tool_meta, auto=auto)
    if name == "read_file":
        return criteria.record(tool_name=name, args=args, ok=tool_ok,
                               evidence=str(tool_meta.get("touched_path") or ""), meta=tool_meta, auto=auto)
    return False


class RunObservations:
    """Own each existing ledger once, with separate pre/post receipt stages."""

    def __init__(self, *, task_spec, durable_state: dict[str, Any], resume: bool,
                 initial_sources: list[dict[str, Any]] | None = None):
        self.evidence = RunEvidence(sources=initial_sources or [], operations_complete=not resume)
        self.project_root: Path | None = None
        self.task_spec = task_spec
        self.criteria = CriteriaTracker.from_spec(task_spec)
        self.outcome = TaskOutcome(durable_state.get("task_outcome"))
        if task_spec is not None:
            requirements = task_requirements(task_spec)
            if resume:
                by_id = {row["id"]: row for row in self.outcome.contract.get("requirements", [])
                         if isinstance(row, dict) and isinstance(row.get("id"), str)}
                for row in requirements:
                    by_id.setdefault(row["id"], row)
                requirements = list(by_id.values())
            self.outcome.set_contract(
                (str(self.outcome.contract.get("goal") or task_spec.goal) if resume
                 else task_spec.goal or str(self.outcome.contract.get("goal") or "")), requirements,
            )
        self.commands = CommandProgress.from_snapshot(durable_state.get("command_progress"))
        self.code_input_epoch = int(durable_state.get("code_input_epoch") or 0)
        self.mutated_files = list(durable_state.get("mutated_files") or [])
        self.verifications = list(durable_state.get("verifications") or [])
        self.failed_attempts = list(durable_state.get("failed_attempts") or [])
        project_epoch = int(durable_state.get("project_epoch") or 0)
        criteria_epoch = int(durable_state.get("criteria_epoch") or 0)
        if resume and criteria_epoch == project_epoch:
            self.criteria.restore_report(list(durable_state.get("criteria") or []))

    def apply_user_clarification(self, text: str, *, root: Path | None = None) -> None:
        previous_spec = {row["id"]: row for row in task_requirements(self.task_spec)}
        self.task_spec = merge_task_spec(self.task_spec, text, project_root=root)
        self.criteria.reconcile(self.task_spec)
        self.criteria.invalidate_after_mutation()
        if self.task_spec is not None:
            requirements = {row["id"]: row for row in self.outcome.contract.get("requirements", [])
                            if isinstance(row, dict) and isinstance(row.get("id"), str)}
            for row in task_requirements(self.task_spec):
                previous = previous_spec.get(row["id"])
                if previous is not None and previous["text"] != row["text"]:
                    # Only an explicit user ID replacement changes an existing
                    # TaskSpec row. Unchanged rows must not undo accepted prose.
                    requirements[row["id"]] = row
                else:
                    requirements.setdefault(row["id"], row)
            self.outcome.set_contract(
                str(self.outcome.contract.get("goal") or self.task_spec.goal), list(requirements.values()),
            )
        # Even prose outside a recognised criteria section changes the inputs.
        self.outcome.apply_user_clarification(text)
        self.verifications.clear()

    def before_dispatch(self, name: str, args: dict, *, root: Path, model_turn: int | None = None) -> dict | None:
        self.project_root = root.resolve()
        return self.commands.before_dispatch(
            name, args, epoch=self.progress_epoch(), cwd=str(root), model_turn=model_turn,
        )

    def progress_epoch(self) -> str:
        # A model's rewritten plan is not changed input or new evidence.
        return f"{self.code_input_epoch}:{len(self.outcome.contract.get('clarifications', []))}"

    def observe_result(self, *, name: str, args: dict, output: dict, status: str,
                       text: str, state_changed: bool, root: Path, bom_selected: bool) -> dict:
        self.project_root = root.resolve()
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
            epoch=self.progress_epoch(), cwd=str(root),
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
        executed_ssh_check = (
            status == "error" and executed_ssh_verification(name, args, output) is not None
        )
        if status == "ok" or executed_ssh_check:
            metadata = ({**output, "_runtime_read_root": str(self.project_root)}
                        if name == "read_file" and self.project_root is not None else output)
            _record_criterion_verdict(self.criteria, name, args, metadata, text, ok)
