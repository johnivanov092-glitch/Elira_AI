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


def test_document_qa_receipt_must_match_published_artifact_hash() -> None:
    evidence = RunEvidence()

    _record(
        evidence,
        "resource_publish",
        output={
            "ok": True,
            "download_name": "proposal.docx",
            "download_url": "/api/skills/download/proposal.docx",
            "sha256": "a" * 64,
            "document_qa": {
                "status": "passed",
                "sha256": "b" * 64,
                "page_count": 1,
                "expected_page_count": 1,
                "renderer": "microsoft_word",
                "vision_status": "passed",
                "issues": [],
            },
        },
    )
    assert evidence.has_verified_document_artifacts is False

    matching = RunEvidence()
    _record(
        matching,
        "resource_publish",
        output={
            "ok": True,
            "download_name": "proposal.docx",
            "download_url": "/api/skills/download/proposal.docx",
            "sha256": "c" * 64,
            "document_qa": {
                "status": "passed",
                "sha256": "c" * 64,
                "page_count": 1,
                "expected_page_count": 1,
                "renderer": "microsoft_word",
                "vision_status": "passed",
                "issues": [],
            },
        },
    )
    assert matching.has_verified_document_artifacts is True


def test_file_gen_qa_and_artifact_share_the_mutation_epoch() -> None:
    evidence = RunEvidence()
    digest = "f" * 64

    _record(
        evidence,
        "file_gen",
        output={
            "ok": True,
            "download_name": "proposal.pdf",
            "sha256": digest,
            "touched_path": "generated/proposal.pdf",
            "document_qa": {
                "status": "passed",
                "target": "proposal.pdf",
                "sha256": digest,
            },
        },
        state_changed=True,
    )

    qa = evidence.receipts_of_kind(EvidenceKind.DOCUMENT_QA)[0]
    artifact = evidence.receipts_of_kind(EvidenceKind.ARTIFACT)[0]
    assert qa.project_epoch == artifact.project_epoch == evidence.project_epoch == 1
    assert evidence.has_verified_document_artifacts is True


def test_failed_document_qa_blocks_model_claim_without_artifact() -> None:
    evidence = RunEvidence()
    _record(
        evidence,
        "resource_publish",
        output={
            "ok": False,
            "project_path": "proposal.docx",
            "document_qa": {
                "status": "failed",
                "sha256": "d" * 64,
                "issues": [{"code": "layout_issue", "message": "Сломан заголовок."}],
            },
            "verifier": True,
        },
    )

    assert evidence.has_unverified_document_qa_claim("Оба КП проверены, QA passed")
    assert evidence.document_qa_backstop().startswith("Документ не опубликован")


def test_failed_document_qa_is_recorded_when_executor_status_is_error() -> None:
    evidence = RunEvidence()
    _record(
        evidence,
        "resource_publish",
        status="error",
        output={
            "ok": False,
            "document_qa": {
                "status": "failed",
                "sha256": "e" * 64,
                "target": "second.docx",
                "issues": [],
            },
        },
    )

    receipts = evidence.receipts_of_kind(EvidenceKind.DOCUMENT_QA)
    assert len(receipts) == 1
    assert receipts[0].target == "second.docx"
    assert receipts[0].passed is False


def test_web_search_is_discovery_not_external_source_evidence() -> None:
    evidence = RunEvidence()

    _record(evidence, "web_search", text="search snippets")
    assert not evidence.has_external_source
    assert evidence.has_web_research

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


def test_current_state_questions_require_external_source() -> None:
    evidence = RunEvidence()

    assert evidence.requires_external_source(
        "Что сейчас с Афганистаном?",
        "Талибы находятся у власти три года.",
    )
    assert evidence.requires_external_source(
        "Как обстоят дела на данный момент?",
        "Ситуация стабильна.",
    )
    assert evidence.requires_external_source(
        "Search the web and verify who founded Poolside AI.",
        "It was founded by two people.",
    )
    assert not evidence.requires_external_source(
        "Что сейчас с сервером?",
        "Load average равен 0.2 по результату ssh_run.",
    )


def test_niche_game_and_film_facts_require_external_source() -> None:
    evidence = RunEvidence()

    assert evidence.requires_external_source(
        "В Паньгу Проклятие души скил, какую руну посоветуешь поставить в умение. "
        "Игра Perfect World RU",
        "Поставь зелёную руну: она даёт +30%.",
    )
    assert evidence.requires_external_source(
        "Расскажи, что это за фильм и что за ситуация в «Четвёртом виде»",
        "Архивные записи настоящие.",
    )
    assert evidence.requires_external_source(
        "А настоящая доктор Эбигейл Тайлер существовала?",
        "Да, это реальный человек.",
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
