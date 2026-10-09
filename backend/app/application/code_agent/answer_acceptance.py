"""Ordered final-answer checks; the coordinator applies returned effects.

This owner retains correction state, but does not call the model, executor,
memory or journal. Existing TaskOutcome/RunEvidence remain the fact owners.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import json
from typing import Any, Literal

from app.application.code_agent.answer_contracts import (
    explicit_quote_request, normalize_quote_word_counts, quote_word_limit_correction,
    quote_word_limit_violations,
)
from app.application.code_agent.run_evidence import RunEvidence, drop_unverified_quotes
from app.application.code_agent.task_outcomes import TaskOutcome
from app.application.context.compaction import RUNTIME_BLOCK_KEY

RetryReason = Literal[
    "background", "evidence", "user_constraint", "delivery", "quote", "quote_source",
]


@dataclass(frozen=True)
class AcceptanceDecision:
    action: Literal["retry", "accept"]
    text: str
    answer_status: Literal["complete", "degraded"] = "complete"
    reason: RetryReason | None = None
    correction: str = ""
    log_note: str | None = None
    activate_groups: tuple[str, ...] = ()
    event: dict[str, Any] | None = None
    event_task_outcome: bool = False
    event_runtime_activation: bool = False
    retain_rejected_answer: bool = True

    @property
    def messages(self) -> tuple[dict[str, str], ...]:
        if self.action != "retry":
            return ()
        # Runtime corrections are projected into the system section; the
        # rejected text is quoted there, never left as the model's last reply.
        correction = {"role": "user", "content": self.correction, RUNTIME_BLOCK_KEY: "answer_correction"}
        if not self.retain_rejected_answer:
            return (correction,)
        return ({"role": "assistant", "content": self.text, RUNTIME_BLOCK_KEY: "rejected_answer"},
                correction)


@dataclass
class AnswerAcceptance:
    download_delivery_correction_sent: bool = False
    evidence_answer_correction_sent: bool = False
    quote_correction_sent: bool = False
    quote_source_correction_sent: bool = False
    user_constraint_correction_sent: bool = False

    def commit(self, decision: AcceptanceDecision) -> None:
        """Commit one-shot flags after the coordinator applies earlier effects."""
        if decision.reason == "evidence":
            self.evidence_answer_correction_sent = True
        elif decision.reason == "delivery":
            self.download_delivery_correction_sent = True
        elif decision.reason == "quote":
            self.quote_correction_sent = True
        elif decision.reason == "quote_source":
            self.quote_source_correction_sent = True
        elif decision.reason == "user_constraint":
            self.user_constraint_correction_sent = True

    def evaluate(
        self, *, final_text: str, raw_user_message: str,
        pending_redirected_jobs: Collection[int], active_capability_groups: Collection[str],
        task_outcome: TaskOutcome, run_evidence: RunEvidence,
        code_input_epoch: int, quote_word_limit: int | None,
        durable_task: str = "", criteria_rows: list[dict] | None = None,
        persistence_policy: dict[str, Any] | None = None, step: int, run_id: str,
    ) -> AcceptanceDecision:
        if pending_redirected_jobs:
            pending_list = ", ".join(str(pid) for pid in sorted(pending_redirected_jobs))
            return AcceptanceDecision(
                "retry", final_text, reason="background", correction=(
                    "[internal background-job correction] Задачу нельзя "
                    "завершать, пока автоматически перенесённая SSH job не "
                    "получила terminal status. Вызови "
                    "run_server(action='logs', kind='job', pid=...) для каждого PID: "
                    f"{pending_list}. Если status=running — опроси позже; "
                    "если completed/failed/cancelled — учти результат и "
                    "только затем отвечай пользователю."
                ),
                log_note="completion blocked by redirected SSH job: " + pending_list,
            )
        runtime_echo = (final_text.lstrip().startswith(("[Системный ответ]", "[ТЕКУЩИЙ КОНТРАКТ ЗАДАЧИ"))
                        and "[ТЕКУЩИЙ КОНТРАКТ ЗАДАЧИ" not in raw_user_message)
        if runtime_echo and not self.evidence_answer_correction_sent:
            return AcceptanceDecision("retry", final_text, reason="evidence", correction=(
                "Вместо ответа выведены внутренние данные runtime. Они не являются результатом задачи. "
                "Выполни исходный запрос существующими инструментами и дай содержательный ответ пользователю. "
                "Для поиска прочитай источники, изложи подтверждённое со ссылками; не печатай контракт или план вместо ответа."
            ))
        if quote_word_limit is not None:
            final_text = normalize_quote_word_counts(final_text)
        # Names in «…» are typography, not quotations, unless quotes were requested.
        skip_quoted_names = not (quote_word_limit is not None or explicit_quote_request(
            raw_user_message or str(durable_task or "")))
        quote_source_problems = [item for item in run_evidence.citations(final_text, skip_names=skip_quoted_names)
                                 if item.get("status") == "unresolved"]
        if quote_source_problems and not self.quote_source_correction_sent:
            return AcceptanceDecision(
                "retry", final_text, reason="quote_source", correction=(
                    "[Проверка источников] Ссылка или дословная цитата не подтверждена показанными источниками: "
                    + json.dumps([{key: item.get(key, "source_unavailable") for key in ("source_id", "reason")}
                                  for item in quote_source_problems], ensure_ascii=False)
                    + ". Исправь только ответ: найди дословный текст в уже показанных веб-выдержках "
                    "и укажи точную [[source:id]] именно этого отрывка рядом с цитатой. "
                    "Совпадение URL страницы не подтверждает другой отрывок. Не выдумывай ID "
                    "и не повторяй успешное чтение. Если цитата не подтверждается, явно сообщи об этом. "
                    + ("Дословные цитаты пользователь не запрашивал: вместо них дай точный пересказ "
                       "прочитанного со ссылками. Верни готовый ответ на исходный вопрос без обсуждения "
                       "служебной проверки и без неподтверждённых цитат." if skip_quoted_names else "")
                ),
                log_note="exact quote does not match cited excerpt",
                event={"type": "answer_format_correction", "step": step, "contract": "quote_source"},
                retain_rejected_answer=(run_evidence.has_mutations or task_outcome.artifact_contract_seen),
            )
        quote_source_failed = bool(quote_source_problems)
        answer_verification = task_outcome.verify_answer(
            final_text, run_evidence, code_input_epoch, persistence_policy=persistence_policy,
            user_request=raw_user_message or str(durable_task or task_outcome.contract.get("goal") or ""))
        # John 2026-10-07: requirements the runtime could not confirm are reported
        # (answer status, readiness UI), never a retry demanding declarations and
        # never a replacement of the model's answer.
        missing_requirements = task_outcome.missing_requirements(
            code_input_epoch, criteria_rows, answer_verification=answer_verification, answer=final_text)
        # The one rule check that still asks for a rewrite: a format condition the
        # user wrote in the message (length, required text) — the answer can fix it.
        # A spent search budget cannot be undone; it only marks the answer partial.
        unmet_user_format = [check for check in answer_verification.get("user_constraint_checks", [])
                             if not check.get("passed") and check.get("kind") == "answer_format"]
        if unmet_user_format and not self.user_constraint_correction_sent:
            return AcceptanceDecision(
                "retry", final_text, reason="user_constraint", correction=(
                    "[Условие пользователя] Ответ не выполняет прямое условие из сообщения пользователя: "
                    + json.dumps([{"field": check["field"], "value": check["value"]}
                                  for check in unmet_user_format], ensure_ascii=False)
                    + ". Перепиши ответ так, чтобы условие выполнялось; содержание и ссылки сохрани."
                ),
                log_note="answer misses an explicit user format condition",
                event={"type": "answer_format_correction", "step": step, "contract": "user_answer_format"},
            )
        missing_downloads = task_outcome.missing_deliveries()
        unbacked_downloads = task_outcome.unbacked_download_links(final_text)
        delivery_problems = {"targets": missing_downloads, "links": unbacked_downloads}
        if (missing_downloads or unbacked_downloads) and not self.download_delivery_correction_sent:
            return AcceptanceDecision(
                "retry", final_text, reason="delivery", correction=(
                    "[internal delivery correction] Для файлов, запрошенных через resource_publish, "
                    "либо выданных ссылок нет подтверждённой публикации текущих байтов: "
                    + json.dumps(delivery_problems, ensure_ascii=False)
                    + ". Проверь файлы и опубликуй через resource_publish; используй только "
                    "ссылку из успешного результата. Удали выдуманную ссылку, если файл не создан. "
                    "Не объявляй неудачную попытку публикации успешной: при невозможности восстановить "
                    "файл объясни конкретную проблему, сохранив остальной результат."
                ),
                log_note="declared/attempted file delivery lacks current publication",
                event={"type": "task_outcome_changed", "step": step},
                event_task_outcome=True,
            )
        download_delivery_failed = bool(missing_downloads or unbacked_downloads)
        if download_delivery_failed:
            final_text = task_outcome.mark_unbacked_download_links(final_text, unbacked_downloads)
            final_text = final_text.rstrip() + (
                "\n\nПубликация для скачивания не подтверждена для текущих файлов или ссылок: "
                + json.dumps(delivery_problems, ensure_ascii=False)
                + ". Успешного подтверждения доставки этих байтов нет."
            )
        unverified_document_qa_claim = run_evidence.has_unverified_document_qa_claim(final_text)
        if unverified_document_qa_claim:
            final_text = run_evidence.document_qa_backstop()
        if quote_word_limit is not None:
            final_text = normalize_quote_word_counts(final_text)
        quote_violations = quote_word_limit_violations(final_text, max_words=quote_word_limit)
        if quote_violations and not self.quote_correction_sent:
            return AcceptanceDecision(
                "retry", final_text, reason="quote",
                correction=quote_word_limit_correction(quote_violations),
                log_note="corrected explicit quote word limit",
                event={"type": "answer_format_correction", "step": step,
                       "contract": "quote_word_limit", "limit": quote_word_limit,
                       "word_counts": [item.word_count for item in quote_violations]},
            )
        quote_format_failed = bool(quote_violations)
        if quote_format_failed:
            final_text = (
                "Не удалось выполнить условие цитирования: превышен лимит "
                f"{quote_word_limit} слов или неверно указано число слов в цитате. "
                "Ответ не прошёл проверку этого условия."
            )
        answer_status = (
            "degraded" if (download_delivery_failed or bool(missing_requirements)
                           or unverified_document_qa_claim or quote_format_failed
                           or quote_source_failed
                           or runtime_echo) else "complete"
        )
        if runtime_echo:
            final_text = "Содержательный ответ не получен: модель повторила внутренние данные задачи."
        if quote_source_failed:
            # Keep the verified answer; drop only the unconfirmed quotes.
            final_text = drop_unverified_quotes(
                final_text, run_evidence.quote_bindings(final_text, skip_names=skip_quoted_names))
            if any(item.get("status") == "unresolved" for item in run_evidence.citations(final_text)):
                final_text += "\n\nЧасть ссылок на источники не подтверждена; связанные с ними выводы требуют проверки."
        # Receipts attest to the delivered bytes, not an earlier candidate that
        # was replaced by a safety fallback or extended with missing coverage.
        task_outcome.verify_answer(final_text, run_evidence, code_input_epoch,
                                   persistence_policy=persistence_policy,
                                   user_request=raw_user_message or str(durable_task or task_outcome.contract.get("goal") or ""))
        return AcceptanceDecision("accept", final_text, answer_status=answer_status)
