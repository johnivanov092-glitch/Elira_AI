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
    normalize_quote_word_counts, quote_word_limit_correction,
    quote_word_limit_violations, web_cadence_citation_correction,
    web_cadence_citation_violations,
)
from app.application.code_agent.capabilities import (
    should_require_local_catalog_search, should_require_web_catalog_fallback,
    should_escalate_web_from_answer,
)
from app.application.code_agent.run_evidence import EvidenceKind, RunEvidence
from app.application.code_agent.task_outcomes import TaskOutcome

RetryReason = Literal[
    "background", "local_catalog", "web_catalog", "catalog_source", "evidence",
    "outcome", "bom", "delivery", "quote", "cadence",
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
    outcome_correction: str | None = None

    @property
    def messages(self) -> tuple[dict[str, str], ...]:
        if self.action != "retry":
            return ()
        return ({"role": "assistant", "content": self.text},
                {"role": "user", "content": self.correction})


@dataclass
class AnswerAcceptance:
    download_delivery_correction_sent: bool = False
    evidence_answer_correction_sent: bool = False
    local_tabular_catalog_probe_seen: bool = False
    library_search_seen: bool = False
    local_catalog_correction_sent: bool = False
    web_catalog_fallback_correction_sent: bool = False
    catalog_web_fallback_required: bool = False
    catalog_web_fetch_seen: bool = False
    catalog_web_fetch_correction_sent: bool = False
    bom_snapshot: dict[str, Any] | None = None
    bom_validation_selected: bool = False
    bom_validation_correction_sent: bool = False
    quote_correction_sent: bool = False
    cadence_correction_sent: bool = False

    def commit(self, decision: AcceptanceDecision) -> None:
        """Commit one-shot flags after the coordinator applies earlier effects."""
        if decision.reason == "local_catalog":
            self.local_catalog_correction_sent = True
        elif decision.reason == "web_catalog":
            self.web_catalog_fallback_correction_sent = True
            self.catalog_web_fallback_required = True
        elif decision.reason == "catalog_source":
            self.catalog_web_fetch_correction_sent = True
        elif decision.reason == "evidence":
            self.evidence_answer_correction_sent = True
        elif decision.reason == "bom":
            self.bom_validation_correction_sent = True
        elif decision.reason == "delivery":
            self.download_delivery_correction_sent = True
        elif decision.reason == "quote":
            self.quote_correction_sent = True
        elif decision.reason == "cadence":
            self.cadence_correction_sent = True

    def evaluate(
        self, *, final_text: str, raw_user_message: str,
        pending_redirected_jobs: Collection[int],
        active_capability_groups: Collection[str],
        task_outcome: TaskOutcome, run_evidence: RunEvidence,
        code_input_epoch: int, quote_word_limit: int | None,
        durable_task: str = "", step: int, run_id: str,
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
        if (not self.local_catalog_correction_sent
                and should_require_local_catalog_search(
                    final_text,
                    local_tabular_probe_seen=self.local_tabular_catalog_probe_seen,
                    library_search_seen=self.library_search_seen)):
            return AcceptanceDecision(
                "retry", final_text, reason="local_catalog", correction=(
                    "[internal local-catalog correction] Нельзя считать нулевой "
                    "exact-match или показанные head()/первые N строк доказательством "
                    "отсутствия позиции в локальном прайсе. Выполни "
                    "runtime_control(operation='library_search', query=...) с "
                    "несколькими независимыми токенами назначения, категории и, если "
                    "известны, бренда/модели. Если документ не находится в Library, "
                    "повтори локальный поиск по отдельным токенам без жёсткой фразы. "
                    "Только после этой проверки можно подтвердить отсутствие или "
                    "искать внешнюю альтернативу. Эта коррекция одноразовая."
                ),
                log_note="completion blocked by unverified local-catalog absence",
            )
        if (not self.web_catalog_fallback_correction_sent
                and should_require_web_catalog_fallback(
                    final_text, bom_validation_selected=self.bom_validation_selected,
                    library_search_seen=self.library_search_seen,
                    external_source_seen=run_evidence.has_external_source)):
            web_was_activated = "web" not in active_capability_groups
            return AcceptanceDecision(
                "retry", final_text, reason="web_catalog", correction=(
                    "[internal catalog Web fallback] Обязательный компонент "
                    "сборки не подтверждён после полного локального Library-поиска. "
                    "Нельзя завершать неполный BOM: вызови web_search, затем "
                    "web_fetch для первичного/магазинного источника и предложи "
                    "совместимую внешнюю альтернативу с подтверждёнными моделью, "
                    "ценой/наличием и URL. Локальные позиции не заменяй внешними, "
                    "если они уже подтверждены."
                ),
                log_note="completion blocked by missing catalog Web fallback",
                activate_groups=("web",) if web_was_activated else (),
                event={"type": "runtime_activation_changed", "run_id": run_id,
                       "step": step, "source": "catalog_absence_fallback"}
                      if web_was_activated else None,
                event_runtime_activation=web_was_activated,
            )
        if (self.catalog_web_fallback_required and not self.catalog_web_fetch_seen
                and not self.catalog_web_fetch_correction_sent):
            return AcceptanceDecision(
                "retry", final_text, reason="catalog_source", correction=(
                    "[internal catalog source correction] Одного web_search "
                    "недостаточно. Вызови web_fetch для выбранного результата и "
                    "подтверди на странице точную модель, совместимость и цену/наличие."
                ),
                log_note="completion blocked until catalog source fetch",
            )
        if ("web" not in active_capability_groups
                and not self.evidence_answer_correction_sent
                and should_escalate_web_from_answer(final_text, raw_user_message)):
            return AcceptanceDecision(
                "retry", final_text, reason="evidence", correction=(
                    "[internal evidence correction] Ответ не проверен инструментами. "
                    "Web tools доступны: отсутствие актуальных знаний модели не "
                    "означает отсутствие доступа к источникам. Самостоятельно выполни "
                    "нужный поиск и прочитай первичные источники, затем ответь на исходный "
                    "вопрос; не спрашивай, выполнять ли поиск или загружать инструменты. "
                    "Явные ограничения пользователя сохраняются. Локальное состояние "
                    "по-прежнему проверяй локальными tools."
                ),
                activate_groups=("web",),
                event={"type": "runtime_activation_changed", "run_id": run_id,
                       "step": step, "source": "evidence_uncertain_answer"},
                event_runtime_activation=True,
            )
        outcome_pending = task_outcome.pending()
        outcome_targets = task_outcome.decision.get("targets") or []
        if not outcome_pending and outcome_targets:
            unverified_targets = set(task_outcome.unverified_targets(outcome_targets, code_input_epoch))
            pending_targets = [target for target in outcome_targets if target in unverified_targets
                               or not run_evidence.has_passing_result_verification([target])]
            if pending_targets:
                outcome_pending = (
                    "Не имеют актуальной проверки следующие заявленные результаты задачи: "
                    + json.dumps(pending_targets, ensure_ascii=False)
                    + ". task_decide.config.targets задаёт результаты всей задачи; "
                    "result_verify.config.targets задаёт только файлы отдельной проверки "
                    "и не изменяет решение task_decide. Если служебный отчёт проверки ошибочно "
                    "включён в результаты задачи, явно обнови task_decide по исходному запросу "
                    "пользователя. Если это действительно нужный дополнительный результат, "
                    "проверь его отдельно. Затем выполни result_verify для текущих результатов: "
                    "config.command читает их и записывает свежий JSON "
                    "{checks:[{name,passed:true/false}]} в report_path, отдельный от проверяемых "
                    "targets. При несовпадении исправь решение и проверь снова."
                )
        if outcome_pending and task_outcome.correction != outcome_pending:
            return AcceptanceDecision(
                "retry", final_text, reason="outcome", correction=outcome_pending,
                outcome_correction=outcome_pending,
                activate_groups=("runtime",) if "runtime" not in active_capability_groups else (),
                event={"type": "task_outcome_changed", "step": step},
                event_task_outcome=True, event_runtime_activation=True,
            )
        if outcome_pending:
            final_text += "\n\nНе подтверждено: " + outcome_pending
        if (self.bom_validation_selected and self.bom_snapshot is None
                and not self.bom_validation_correction_sent):
            return AcceptanceDecision(
                "retry", final_text, reason="bom", correction=(
                    "[internal BOM correction] Нельзя завершать локальную "
                    "спецификацию/КП с арифметикой модели. Вызови bom_validate "
                    "по исходному XLSX/CSV: передай точные колонки, выбранные коды "
                    "и количества, остаток, наценку, НДС и услуги. Используй только "
                    "подтверждённые rows/total из результата. Если ok=false — исправь "
                    "подбор; не создавай и не публикуй финальный документ до ok=true."
                ),
                log_note="completion blocked by missing deterministic BOM validation",
            )
        missing_downloads = task_outcome.missing_deliveries()
        unbacked_downloads = task_outcome.unbacked_download_links(final_text)
        delivery_problems = {"targets": missing_downloads, "links": unbacked_downloads}
        if (missing_downloads or unbacked_downloads) and not self.download_delivery_correction_sent:
            return AcceptanceDecision(
                "retry", final_text, reason="delivery", correction=(
                    "[internal delivery correction] Для объявленных или фактически "
                    "запрошенных через resource_publish файлов либо выданных ссылок нет подтверждённой "
                    "публикации текущих байтов: "
                    + json.dumps(delivery_problems, ensure_ascii=False)
                    + ". Проверь файлы и опубликуй через resource_publish; используй только "
                    "ссылку из успешного результата. Удали выдуманную ссылку, если файл не создан. "
                    "Если объявленный delivery contract неверно отражает задачу, "
                    "исправь его явно через task_decide с reason; пропуск поля "
                    "delivery сохраняет прежний контракт. Не объявляй неудачную "
                    "попытку публикации успешной: при невозможности восстановить "
                    "файл объясни конкретную проблему, сохранив остальной результат."
                ),
                log_note="declared/attempted file delivery lacks current publication",
                event={"type": "task_outcome_changed", "step": step},
                event_task_outcome=True,
            )
        download_delivery_failed = bool(missing_downloads or unbacked_downloads)
        bom_validation_failed = bool(self.bom_validation_selected and self.bom_snapshot is None)
        catalog_web_failed = bool(self.catalog_web_fallback_required and not self.catalog_web_fetch_seen)
        if bom_validation_failed:
            final_text = (
                "BOM не завершён: детерминированная проверка кодов, остатков, "
                "цен, НДС и итогов не получила статус ok=true. Непроверенные "
                "позиции и суммы не публикуются."
            )
        elif catalog_web_failed:
            final_text = (
                "BOM не завершён: внешняя альтернатива не подтверждена чтением "
                "источника. Результат Web-поиска без web_fetch не считается "
                "проверкой модели, совместимости и наличия."
            )
        elif self.bom_validation_selected and self.bom_snapshot is not None:
            artifact_note = (
                " Финальный файл опубликован."
                if not download_delivery_failed and run_evidence.receipts_of_kind(EvidenceKind.ARTIFACT)
                else ""
            )
            final_text = (
                "BOM детерминированно проверен по локальному каталогу. "
                f"Канонический итог: {self.bom_snapshot['total']}."
                f"{artifact_note} Receipt: "
                f"{self.bom_snapshot['receipt_sha256']}."
            )
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
        cadence_violations = ()
        presented_sources = run_evidence.presented_sources
        source_request = raw_user_message or str(durable_task or "")
        if presented_sources and run_evidence.requires_external_source(source_request, final_text):
            cadence_violations = web_cadence_citation_violations(
                final_text,
                matched_source_ids={source["id"] for source in presented_sources},
                read_source_urls={source["url"] for source in presented_sources},
                read_sources=presented_sources,
            )
        if cadence_violations and not self.cadence_correction_sent:
            return AcceptanceDecision(
                "retry", final_text, reason="cadence",
                correction=web_cadence_citation_correction(cadence_violations),
                log_note="requested local source binding for update cadence",
                event={"type": "answer_format_correction", "step": step,
                       "contract": "web_cadence_citation",
                       "quantities": [item.quantity for item in cadence_violations]},
            )
        cadence_format_failed = bool(cadence_violations)
        if cadence_format_failed:
            final_text = (
                "Не удалось подкрепить указанный период обновлений ссылкой "
                "на прочитанный источник для того же объекта. "
                "Ответ не прошёл проверку ссылок."
            )
        answer_status = (
            "degraded" if (download_delivery_failed or bool(outcome_pending)
                           or bom_validation_failed or catalog_web_failed
                           or unverified_document_qa_claim or quote_format_failed
                           or cadence_format_failed) else "complete"
        )
        return AcceptanceDecision("accept", final_text, answer_status=answer_status)
