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
    explicit_quote_request, explicit_web_check_requested, normalize_quote_word_counts, quote_word_limit_correction,
    quote_word_limit_violations, web_cadence_citation_correction,
    web_cadence_citation_violations, web_source_citation_violations,
)
from app.application.code_agent.capabilities import (
    should_require_local_catalog_search, should_require_web_catalog_fallback,
    should_escalate_web_from_answer,
)
from app.application.code_agent.run_evidence import EvidenceKind, RunEvidence, drop_unverified_quotes
from app.application.code_agent.task_outcomes import TaskOutcome
from app.application.context.compaction import RUNTIME_BLOCK_KEY

RetryReason = Literal[
    "background", "local_catalog", "web_catalog", "catalog_source", "evidence",
    "outcome", "bom", "delivery", "quote", "quote_source", "cadence", "web_source",
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
    quote_source_correction_sent: bool = False
    cadence_correction_sent: bool = False
    web_source_correction_sent: bool = False

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
        elif decision.reason == "quote_source":
            self.quote_source_correction_sent = True
        elif decision.reason == "cadence":
            self.cadence_correction_sent = True
        elif decision.reason == "web_source":
            self.web_source_correction_sent = True

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
        missing_web_check = (explicit_web_check_requested(raw_user_message)
                             and not run_evidence.has_web_research and not run_evidence.has_external_source
                             and not any(source.get("status") in {"discovered", "fetched", "excerpt"}
                                         for source in run_evidence.sources))
        if missing_web_check and not self.evidence_answer_correction_sent:
            return AcceptanceDecision("retry", final_text, reason="evidence", correction=(
                "[Проверка выполнения] Пользователь поручил проверить внешние источники, но runtime "
                "не получил ни одного результата поиска или чтения. Напечатанный текст web_search/JSON "
                "не является вызовом инструмента. Вызови web_search или web_fetch через structured tool_calls, "
                "прочитай нужный источник и ответь на исходный вопрос. Не выдумывай результаты или ссылки."
            ), activate_groups=("web",) if "web" not in active_capability_groups else ())
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
        web_source_problems = ()
        if run_evidence.sources and not task_outcome.artifact_contract_seen:
            web_source_problems = web_source_citation_violations(
                final_text,
                read_source_urls={source["url"] for source in run_evidence.presented_sources
                                  if source.get("quote_verified") is True},
                known_source_urls={source["url"] for source in run_evidence.sources},
            )
        if web_source_problems and not self.web_source_correction_sent:
            return AcceptanceDecision(
                "retry", final_text, reason="web_source", correction=(
                    "[Проверка прочитанных источников] Ссылки рядом с утверждениями ведут на страницы, "
                    "чей текст не прочитан: "
                    + json.dumps([{"block": item.block_index, "url": item.url, "reason": item.reason}
                                  for item in web_source_problems[:8]], ensure_ascii=False)
                    + ". Исправь ответ по уже показанным выдержкам: укажи именно прочитанный источник "
                    "или удали неподтверждённое утверждение. Если этот факт нужен для исходного вопроса, "
                    "прочитай конкретную недостающую страницу. Сниппет, оглавление и ссылка из соседней "
                    "статьи не подтверждают содержание целевой страницы. Не оставляй утверждение только "
                    "с оговоркой о непроверенной ссылке и не заменяй ссылку ради прохождения проверки. "
                    "Не вызывай task_decide ради ответа и не повторяй уже успешное чтение."
                ),
                log_note="ordinary Web answer cites unread sources",
                event={"type": "answer_format_correction", "step": step,
                       "contract": "web_source_citation"},
                retain_rejected_answer=(run_evidence.has_mutations or task_outcome.artifact_contract_seen),
            )
        web_source_failed = bool(web_source_problems)
        if web_source_failed:
            # A warning below an unsupported assertion still leaves that
            # assertion in the delivered answer. Replace the body instead.
            final_text = (
                "Подтвердить исходный ответ прочитанными источниками не удалось. "
                "Непроверенные утверждения исключены."
            )
            read_urls = list(dict.fromkeys(source["url"] for source in run_evidence.presented_sources
                                          if source.get("quote_verified") is True))[:5]
            if read_urls:
                final_text += "\n\nПрочитанные источники:\n" + "\n".join(
                    f"- [Источник]({url})" for url in read_urls)
        answer_verification = task_outcome.verify_answer(
            final_text, run_evidence, code_input_epoch, persistence_policy=persistence_policy,
            user_request=raw_user_message or str(durable_task or task_outcome.contract.get("goal") or ""))
        outcome_pending = task_outcome.pending()
        missing_requirements = task_outcome.missing_requirements(
            code_input_epoch, criteria_rows, answer_verification=answer_verification, answer=final_text)
        if missing_requirements:
            outcome_pending = (
                "Не подтверждены обязательные требования текущей задачи: "
                + json.dumps(missing_requirements, ensure_ascii=False)
                + ". Сохрани исходную цель и остальные требования. Для ответа прямо в чате "
                "исправь содержание по уже прочитанным источникам и прямым условиям пользователя. "
                "Не составляй task_decide ради ответа. Если фактов не хватает, проверь недостающий "
                "источник или явно обозначь пробел; не повторяй уже успешные поиски без новых данных. "
                "Успешный поиск не покрывает непроверяемые смысловые условия. Для файлов проверь каждый пункт "
                "подходящим реальным verifier. Для result_verify свежий JSON отчёт должен "
                "содержать checks:[{name,requirement_id,passed:boolean}] с ID из текущего "
                "контракта. passed=true для части проверок не покрывает остальные пункты. "
                "После уточнения или изменения входов выполни проверки заново."
            )
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
            failed_checks = [
                {"requirement_id": row["requirement_id"],
                 "failed_checks": [item["kind"] for item in row["predicates"] if not item["passed"]]}
                for row in answer_verification.get("checks", []) if not row["passed"]
            ]
            # Keep the retry identity stable: a changing candidate/predicate must
            # not create an additional correction attempt for the same gap.
            feedback = outcome_pending
            if failed_checks:
                feedback += (
                    " Не прошли конкретные проверки текущего ответа: "
                    + json.dumps(failed_checks, ensure_ascii=False)
                    + ". Исправь указанные проверки, сохрани успешные. Не повторяй уже успешный "
                    "поиск или чтение без причины. Проверка формата не подтверждает достоверность фактов."
                )
            return AcceptanceDecision(
                "retry", final_text, reason="outcome", correction=feedback,
                outcome_correction=outcome_pending,
                activate_groups=("runtime",) if "runtime" not in active_capability_groups else (),
                event={"type": "task_outcome_changed", "step": step},
                event_task_outcome=True, event_runtime_activation=True,
            )
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
                retain_rejected_answer=(run_evidence.has_mutations or task_outcome.artifact_contract_seen),
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
                           or cadence_format_failed or quote_source_failed or web_source_failed
                           or runtime_echo or missing_web_check) else "complete"
        )
        if runtime_echo:
            final_text = "Содержательный ответ не получен: модель повторила внутренние данные задачи."
        if missing_web_check:
            final_text = "Запрошенная проверка внешних источников не выполнена. Фактических результатов поиска или чтения нет; подтвердить ответ не удалось."
        if quote_source_failed:
            # Keep the verified answer; drop only the unconfirmed quotes.
            final_text = drop_unverified_quotes(
                final_text, run_evidence.quote_bindings(final_text, skip_names=skip_quoted_names))
            if any(item.get("status") == "unresolved" for item in run_evidence.citations(final_text)):
                final_text += "\n\nЧасть ссылок на источники не подтверждена; связанные с ними выводы требуют проверки."
        # A read-only sourced answer remains useful with explicitly reported
        # gaps. Artifact completion claims still require their actual verifiers.
        preserve_partial = (run_evidence.has_external_source and not run_evidence.has_mutations
                            and not task_outcome.artifact_contract_seen)
        if missing_requirements:
            final_text = (final_text.rstrip() + "\n\n" if preserve_partial else "") + "Не удалось подтвердить:\n" + "\n".join(
                f"- {item['text']} ({item['status']})" for item in missing_requirements)
        elif outcome_pending:
            final_text = ((final_text.rstrip() + "\n\n" if preserve_partial else "")
                          + "Полное выполнение задачи не подтверждено. " + outcome_pending)
        # Receipts attest to the delivered bytes, not an earlier candidate that
        # was replaced by a safety fallback or extended with missing coverage.
        task_outcome.verify_answer(final_text, run_evidence, code_input_epoch,
                                   persistence_policy=persistence_policy,
                                   user_request=raw_user_message or str(durable_task or task_outcome.contract.get("goal") or ""))
        return AcceptanceDecision("accept", final_text, answer_status=answer_status)
