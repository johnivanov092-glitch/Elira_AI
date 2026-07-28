from __future__ import annotations

from app.application.code_agent.run_evidence import EvidenceKind, RunEvidence
from app.application.code_agent.taskspec import CriteriaTracker, TaskSpec


def _record(
    evidence: RunEvidence,
    tool: str,
    *,
    args: dict | None = None,
    status: str = "ok",
    output: dict | None = None,
    text: str = "",
    state_changed: bool = False,
) -> None:
    evidence.record_tool_result(
        tool_name=tool,
        arguments=args or {},
        execution_status=status,
        output=output or {},
        text_result=text,
        state_changed=state_changed,
    )


def test_crm_verification_is_bound_to_latest_project_epoch() -> None:
    evidence = RunEvidence()

    _record(
        evidence,
        "write_file",
        args={"path": "src/App.tsx"},
        output={"ok": True, "touched_path": "src/App.tsx"},
        state_changed=True,
    )
    assert evidence.project_epoch == 1
    assert evidence.has_mutations
    assert not evidence.has_current_verification

    _record(
        evidence,
        "run_bash",
        args={"command": "npm test"},
        output={"exit_code": 0},
        text="exit=0",
    )
    assert evidence.has_current_verification
    assert evidence.has_current_passing_verification

    _record(
        evidence,
        "edit_file",
        args={"path": "src/App.tsx"},
        output={"ok": True, "touched_path": "src/App.tsx"},
        state_changed=True,
    )
    assert evidence.project_epoch == 2
    assert not evidence.has_current_verification
    assert not evidence.has_current_passing_verification

    _record(
        evidence,
        "run_bash",
        args={"command": "npm test"},
        output={"exit_code": 1},
        text="exit=1",
    )
    assert evidence.has_current_verification
    assert not evidence.has_current_passing_verification


def test_non_verifier_shell_command_does_not_create_verification_receipt() -> None:
    evidence = RunEvidence()
    _record(
        evidence,
        "run_bash",
        args={"command": "mkdir test"},
        output={"exit_code": 0},
        text="exit=0",
    )
    assert not evidence.has_current_verification
    assert not evidence.has_current_passing_verification


def test_ssh_requires_executed_observation_and_postcheck_after_change() -> None:
    evidence = RunEvidence()

    _record(
        evidence,
        "ssh_read",
        args={"host": "ai-server", "path": "/etc/app.conf"},
        status="blocked",
        output={"ok": False, "error": "approval_rejected"},
    )
    assert evidence.remote_hosts == ()
    assert not evidence.receipts_of_kind(EvidenceKind.OBSERVATION)

    _record(
        evidence,
        "ssh_read",
        args={"host": "ai-server", "path": "/etc/app.conf"},
        text="setting=true",
    )
    assert evidence.remote_hosts == ("ai-server",)

    _record(
        evidence,
        "ssh_write",
        args={"host": "ai-server", "path": "/etc/app.conf"},
        output={"ok": True, "touched_path": "/etc/app.conf"},
        state_changed=True,
    )
    assert evidence.has_mutations
    assert not evidence.has_current_verification

    _record(
        evidence,
        "ssh_assert_contains",
        args={"host": "ai-server", "path": "/etc/app.conf", "text": "setting=true"},
        output={"ok": True, "verifier": True},
        text="assertion passed",
    )
    assert evidence.has_current_passing_verification


def test_document_claim_requires_successful_exact_file_gen_artifact() -> None:
    evidence = RunEvidence()

    _record(
        evidence,
        "resource_publish",
        output={"ok": True, "download_name": "report.pdf"},
        text="published",
    )
    assert evidence.unbacked_document_claims("Готово, вот report.pdf") == ["report.pdf"]

    _record(
        evidence,
        "file_gen",
        status="error",
        output={"ok": False, "download_name": "report.pdf"},
        text="generation failed",
    )
    assert evidence.unbacked_document_claims("Готово, вот report.pdf") == ["report.pdf"]

    _record(
        evidence,
        "file_gen",
        output={"ok": True, "download_name": "actual.pdf"},
        text="created",
    )
    assert evidence.unbacked_document_claims("Готово, вот report.pdf") == ["report.pdf"]

    _record(
        evidence,
        "file_gen",
        output={"ok": True, "download_name": "report.pdf"},
        text="created",
    )
    assert evidence.unbacked_document_claims("Готово, вот report.pdf") == []


def test_web_search_is_discovery_not_external_source_evidence() -> None:
    evidence = RunEvidence()

    _record(evidence, "web_search", text="search snippets")
    assert not evidence.has_external_source

    _record(
        evidence,
        "web_fetch",
        status="error",
        output={"ok": False},
        text="fetch failed",
    )
    assert not evidence.has_external_source

    _record(evidence, "web_fetch", text="full source contents")
    assert evidence.has_external_source
    assert evidence.requires_external_source(
        "Проверь в интернете, кто основал компанию",
        "Компания основана Иваном.",
    )


def test_runtime_summary_contains_only_structured_counts() -> None:
    evidence = RunEvidence()
    _record(evidence, "read_file", args={"path": "src/App.tsx"}, text="secret source")
    _record(
        evidence,
        "write_file",
        args={"path": "src/App.tsx"},
        output={"ok": True, "touched_path": "src/App.tsx"},
        state_changed=True,
    )

    summary = evidence.summary()

    assert summary == {
        "project_epoch": 1,
        "mutations": 1,
        "observations": 1,
        "verifications": 0,
        "artifacts": 0,
        "external_sources": 0,
        "current_verification": False,
        "current_passing_verification": False,
    }
    assert "secret source" not in repr(summary)


def test_crm_criterion_verdict_is_invalidated_after_later_mutation() -> None:
    criteria = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
        "production build проходит",
    ]))
    changed = criteria.record(
        tool_name="run_bash",
        args={"command": "npm run build"},
        ok=True,
        evidence="exit=0",
        meta={"exit_code": 0},
    )
    assert changed
    assert criteria.completion_status() == "confirmed"

    assert criteria.invalidate_after_mutation() == 1
    assert criteria.completion_status() == "unverified"
    assert criteria.report()[0]["evidence"] is None
