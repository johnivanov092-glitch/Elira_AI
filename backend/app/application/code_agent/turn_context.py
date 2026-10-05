"""Initial messages and persisted planning context for the code-agent run.

Prompt/history/TaskSpec algorithms remain with their existing owners. This
module assembles their output in the established message order; it never calls
the model or decides whether a final answer is accepted.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.application.code_agent.document_validation import infer_expected_page_count
from app.application.code_agent.history import _coerce_history
from app.application.code_agent.loop_helpers import (
    _schema_tool_name, build_task_state_block, upsert_task_state_message,
)
from app.application.code_agent.planning import PlanArtifact, plan_artifact_from_dict
from app.application.code_agent.prompts import (
    _build_system_prompt, _build_turn_context, _build_project_context,
    _shell_guidance, _NO_PROJECT_BLOCK, _PROJECT_CONNECTED_BLOCK,
    _is_scratch_workspace,
)
from app.application.code_agent.taskspec import (
    TaskSpec, derive_task_spec, is_continuation_message, merge_task_spec, taskspec_context,
)
from app.application.context.compaction import (
    TASK_CONTRACT_PREFIX, TASK_CONTRACT_MARKER_VALUE, TASK_STATE_MARKER_KEY,
)
from app.application.tool_providers import ToolRegistry
from app.application.tool_providers.mcp_provider import creative_workflow_prompt
from app.application.code_agent.task_guidance import (
    DELIVERY_GUIDANCE, WEB_SOURCE_FIDELITY_GUIDANCE, task_guidance_blocks,
)
from app.application.code_agent.task_skills import (
    ADVISOR_CONTEXT_ID, CATALOG_ID, CONTEXT_ID, SkillContext,
    advisor_context, catalog_context, insert_skill_context,
)


logger = logging.getLogger(__name__)
PromptBuilder = Callable[..., str]
_WEB_CLOSING_CONTEXT_ID = "web-closing-context"


@dataclass(frozen=True)
class InitialTurn:
    messages: list[dict[str, Any]]
    task_spec: TaskSpec | None
    task_spec_source: str
    document_page_count_contract: int | None


def build_initial_turn(
    *,
    root: Path,
    user_message: str,
    raw_user_message: str,
    memory_query: str | None,
    working_dir: Path | str | None,
    model: str,
    profile_name: str,
    conversation_history: list[dict[str, Any]] | None,
    resource_refs: list[dict[str, Any]] | None,
    active_schemas: list[dict[str, Any]],
    system_prompt_builder: PromptBuilder = _build_system_prompt,
    turn_context_builder: PromptBuilder = _build_turn_context,
) -> InitialTurn:
    """Assemble the stable system/history prefix and current-request tail."""
    initial_tools = tuple(dict.fromkeys(
        name for schema in active_schemas
        if (name := _schema_tool_name(schema))
    ))
    system_prompt = system_prompt_builder(
        root, model_name=model, profile_name=profile_name,
    )
    request_context = turn_context_builder(
        root, working_dir=working_dir, active_tools=initial_tools,
        model_name=model, profile_name=profile_name, task_text=raw_user_message,
        memory_query=memory_query or "", resource_refs=resource_refs,
    )
    creative_context = [str(user_message or "")]
    for turn in list(conversation_history or [])[-8:]:
        if isinstance(turn, dict) and isinstance(turn.get("content"), str):
            creative_context.append(str(turn["content"])[:4000])
    if re.search(
        r"(?:\bblender\b|\bunity\b|блендер|юнити|сцен\w*|scene\w*|"
        r"префаб\w*|prefab\w*|террейн\w*|terrain\w*|\b3d\b)",
        "\n".join(creative_context),
        re.IGNORECASE,
    ):
        creative_prompt = creative_workflow_prompt({
            _schema_tool_name(schema) for schema in active_schemas
            if _schema_tool_name(schema)
        })
        if creative_prompt:
            request_context += "\n\n" + creative_prompt
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(_coerce_history(conversation_history))
    # Keep the current request last; persona/context tails precede its raw text.
    effective_user_message = ""
    if request_context:
        effective_user_message = "[Контекст текущего запроса]\n" + request_context
    task_spec = derive_task_spec(user_message, project_root=root)
    task_spec_source = "current_message" if task_spec is not None else "none"
    document_page_count_contract = infer_expected_page_count(user_message)
    if task_spec is None and is_continuation_message(user_message):
        # A new question must not inherit a previous structured task's criteria.
        for index in range(len(conversation_history or []) - 1, -1, -1):
            turn = conversation_history[index]
            if isinstance(turn, dict) and turn.get("role") == "user":
                history_spec = derive_task_spec(
                    str(turn.get("content") or ""), project_root=root,
                )
                if history_spec is not None:
                    task_spec = history_spec
                    task_spec_source = "conversation_history"
                    # Later clarifications amend this task instead of erasing it.
                    for later in (conversation_history or [])[index + 1:]:
                        if isinstance(later, dict) and later.get("role") == "user":
                            task_spec = merge_task_spec(task_spec, str(later.get("content") or ""), project_root=root)
                    break
    if document_page_count_contract is None and is_continuation_message(user_message):
        for turn in reversed(conversation_history or []):
            if isinstance(turn, dict) and turn.get("role") == "user":
                document_page_count_contract = infer_expected_page_count(
                    str(turn.get("content") or ""),
                )
                if document_page_count_contract is not None:
                    break
    if task_spec is not None:
        effective_user_message = f"{taskspec_context(task_spec)}\n\n{effective_user_message}"
    # Capture transient tone once per run without rewriting the cached prefix.
    try:
        from app.application.persona.mood import mood_overlay_line

        effective_user_message += "\n\n[Текущий тон Elira]\n" + mood_overlay_line()
    except Exception:
        logger.debug("transient persona tone unavailable", exc_info=True)
    effective_user_message = (
        effective_user_message.strip()
        + "\n\n[Текущее сообщение пользователя]\n"
        + user_message
    ).lstrip()
    messages.append({"role": "user", "content": effective_user_message})
    return InitialTurn(
        messages, task_spec, task_spec_source, document_page_count_contract,
    )


def _load_planning_state(run_id: str) -> tuple[PlanArtifact | None, bool]:
    """Restore a reusable plan or its attempted flag without replanning."""
    try:
        from app.application.code_agent.run_journal import RunJournal

        state = RunJournal.load(run_id).state
    except Exception:
        return None, False
    stored = state.get("plan")
    plan = plan_artifact_from_dict(stored) if isinstance(stored, dict) else None
    return plan, bool(state.get("planning_attempted"))


def _bounded_planning_recon(registry: ToolRegistry, *, char_cap: int) -> str:
    """Read a bounded project map through the same registry, best-effort."""
    try:
        result = registry.dispatch_raw("project_map", {"max_depth": 2})
        text = str((result or {}).get("text") or "")
        return text[:max(1, int(char_cap))]
    except Exception:
        return ""


class SkillRestoreError(Exception):
    """Only a failed persisted skill restore, before catalog initialization."""


class TurnContext:
    """Mutable messages and pinned instructions belonging to one run."""

    def __init__(self, *, messages: list[dict[str, Any]], raw_user_message: str,
                 root: Path, working_dir: Path | str | None, run_id: str):
        self.messages = messages
        self.raw_user_message = raw_user_message
        self.original_goal = raw_user_message
        self.clarifications: list[str] = []
        self.root, self.working_dir, self.run_id = root, working_dir, run_id

    def initialize_skills(self, *, resume: bool) -> None:
        self.sent_guidance: set[str] = set()
        self.guidance_message_ids: set[str] = set()
        self.skills = SkillContext()
        if resume:
            try:
                self.skills.restore(self.run_id)
            except (OSError, ValueError) as exc:
                raise SkillRestoreError(str(exc)) from exc
        self.catalog = catalog_context()
        if self.catalog:
            self.messages = insert_skill_context(self.messages, self.catalog, CATALOG_ID)
            self.guidance_message_ids.add(CATALOG_ID)
        advisor_text, advisor_state = advisor_context(self.raw_user_message)
        if advisor_text:
            self.messages = insert_skill_context(self.messages, advisor_text, ADVISOR_CONTEXT_ID)
            self.guidance_message_ids.add(ADVISOR_CONTEXT_ID)
        self.advisor_state = advisor_state
        self.skill_reminder_pending = False
        self.skill_reminder_sent = False
        self.refresh_task_state = True
        self.awaiting_input_replies: list[str] = []
        self.user_input_policy_applied = False


    def apply_user_inputs(self, rows: list[dict], *, step: int):
        for row in rows:
            self.messages.append({"role": "user", "content": row["text"]})
            self.raw_user_message += "\n\nУточнение пользователя:\n" + row["text"]
            self.clarifications.append(row["text"])
            self.awaiting_input_replies.append(row["request_id"])
            yield {"type": "user_input_applied", "step": step, "run_id": self.run_id,
                   "request_id": row["request_id"], "text": row["text"]}
        if not self.user_input_policy_applied:
            # Qwen permits system instructions only at index 0. Keep the
            # user's text on its own channel and add this static policy once.
            self.messages[0]["content"] += (
                "\n\nПользователь прислал уточнение во время текущей задачи. Кратко, одной-двумя фразами, "
                "подтверди, что учла его, и при необходимости скажи, как меняется работа. Затем продолжай "
                "эту же задачу с учётом уточнения. Сохраняй исходную цель, пока пользователь явно её не заменил. "
                "Уже выполненные действия не отменяются этим сообщением. Если требования изменились, "
                "обнови текущий чеклист и контракт результата через существующие инструменты."
            )
            self.user_input_policy_applied = True
        self.refresh_task_state = True


    def add_skill_reminder(self) -> None:
        if self.catalog and self.skill_reminder_pending and not self.skill_reminder_sent and not self.skills.snapshots():
            # A bounded reminder after actual project/tool discovery. This
            # uses the next existing inference turn, never blocks tools and
            # never assigns a domain/persona from user keywords.
            self.messages = insert_skill_context(self.messages, (
                "[Рабочее напоминание Elira] Ты начала работу с проектом или системой. "
                "Проверь каталог навыков выше и перед дальнейшей профильной работой "
                "загрузи подходящие инструкции через runtime_control(operation='skill_load', "
                "name=имя, query=причина). Для кода обычно нужен навык языка и code-change; "
                "для поиска причины сбоя — diagnostics. Выбери сама по текущей задаче. "
                "Если подходящего навыка нет или задача не требует профильной инструкции, "
                "продолжай доступными инструментами."
            ), "elira-skill-reminder")
            self.skill_reminder_sent = True


    def add_guidance(self, *, schemas: list[dict], request_route,
                      task_instructions: str, step: int, work_started: bool = True) -> None:
        guidance = task_guidance_blocks(
            {_schema_tool_name(schema) for schema in schemas},
            domain_policies=request_route.domain_policies,
        )
        if not work_started:
            guidance.pop("work", None)
        if "web" in guidance and "web" not in self.sent_guidance:
            self.messages[0] = {**self.messages[0], "content": (
                str(self.messages[0].get("content") or "")
                + "\n\n[Контракт ответа по прочитанным источникам]\n" + WEB_SOURCE_FIDELITY_GUIDANCE
            )}
        if request_route.download_requested or "resources" in guidance:
            guidance["file_delivery"] = DELIVERY_GUIDANCE
        if task_instructions:
            guidance["delivery"] = task_instructions
        if ("work" in guidance or "web" in guidance) and "project_context" not in self.sent_guidance:
            guidance["project_context"] = _build_project_context(self.root, self.working_dir)
        if "project" in guidance and "project" not in self.sent_guidance:
            guidance["project"] += "\n" + _shell_guidance()
            guidance["project"] += (
                _NO_PROJECT_BLOCK if _is_scratch_workspace(self.root) else _PROJECT_CONNECTED_BLOCK
            )
        new_guidance = [text for key, text in guidance.items() if key not in self.sent_guidance]
        if new_guidance:
            block = "[Инструкции текущей задачи]\n" + "\n\n".join(new_guidance)
            message_id = f"{self.run_id}:guidance:{step}"
            guidance_message = {"role": "user", "content": block, "_msg_id": message_id}
            if step == 1:
                # Put initial work instructions before the current request,
                # so the model answers the user rather than the instructions.
                self.messages.insert(len(self.messages) - 1, guidance_message)
            else:
                self.messages.append(guidance_message)
            self.guidance_message_ids.add(message_id)
            self.sent_guidance.update(guidance)
        active_skill_text = self.skills.context()
        if active_skill_text:
            self.messages = insert_skill_context(self.messages, active_skill_text, CONTEXT_ID)
            self.guidance_message_ids.add(CONTEXT_ID)

    def update_task_state(self, *, task_spec, criteria_rows: list[dict],
                          checklist_items: list[dict], mutated_files: list[str],
                          verifications: list[str], failed_attempts: list[str]) -> None:
        # Keep every requirement, but leave repeated verifier receipts in the
        # journal/outcome owner rather than pinning their metadata twice.
        requirements = [{key: row[key] for key in (
            "requirement_id", "text", "mandatory", "status", "lifecycle", "host", "target", "condition",
        ) if key in row} for row in criteria_rows]
        contract = {"original_goal": self.original_goal,
                    "goal": str(getattr(task_spec, "goal", "") or self.original_goal),
                    "clarifications": self.clarifications, "requirements": requirements}
        contract_message = {"role": "assistant", "content": TASK_CONTRACT_PREFIX + json.dumps(
            contract, ensure_ascii=False, separators=(",", ":")),
            TASK_STATE_MARKER_KEY: TASK_CONTRACT_MARKER_VALUE}
        self.messages = [message for message in self.messages
                         if message.get(TASK_STATE_MARKER_KEY) != TASK_CONTRACT_MARKER_VALUE]
        self.messages.insert(1, contract_message)
        if (task_spec is not None or checklist_items) and self.refresh_task_state:
            self.messages = upsert_task_state_message(
                self.messages, build_task_state_block(
                    goal=str(getattr(task_spec, "goal", "")),
                    constraints=list(getattr(task_spec, "constraints", None) or []),
                    criteria_rows=criteria_rows, checklist_items=checklist_items,
                    mutated_files=mutated_files, verifications=verifications,
                    failed_attempts=failed_attempts, next_step="",
                ),
            )
            self.refresh_task_state = False

    def pin_outcome_context(self, text: str) -> None:
        context_id = f"{self.run_id}:task-outcome"
        self.messages = insert_skill_context(self.messages, text, context_id)
        self.guidance_message_ids.add(context_id)

    def pin_recovery_context(self, text: str) -> None:
        context_id = f"{self.run_id}:command-recovery"
        self.messages = insert_skill_context(self.messages, text, context_id)
        self.guidance_message_ids.add(context_id)

    def refresh_web_closing_context(self, messages: list[dict[str, Any]], *,
                                    read_pages: tuple[str, ...]) -> list[dict[str, Any]]:
        """One input-side closing cue after complete result groups, never fact prose."""
        messages = [message for message in messages
                    if message.get("_msg_id") != _WEB_CLOSING_CONTEXT_ID]
        pending = 0
        for message in messages:
            if message.get("role") == "assistant" and message.get("tool_calls"):
                pending += len(message["tool_calls"])
            elif message.get("role") == "tool":
                pending = max(0, pending - 1)
        if not read_pages or pending:
            return messages
        content = (
            "[Следующий шаг: инструкция runtime, не выводи]\n"
            "Прочитаны страницы: " + ", ".join(read_pages) + ".\n"
            "Если исходный вопрос покрыт, дай итоговый ответ. Для файла или действия "
            "продолжай до результата: чтение его не заменяет. Проверяй лишь конкретный "
            "пробел исходной цели; другие источники доступны. Это список прочитанного, не выводы. "
            + WEB_SOURCE_FIDELITY_GUIDANCE + " Промежуточная сводка не требуется."
        )
        return [*messages, {"role": "user", "content": content, "_msg_id": _WEB_CLOSING_CONTEXT_ID}]

    def provider_messages(self, *,
                          read_source_handles: Callable[[list[dict[str, Any]]], tuple[str, ...]] | None = None,
                          project_search_without_snippets: Callable[[str], str] | None = None,
                          ) -> list[dict[str, Any]]:
        """Project older discovery text without changing retained tool history."""
        if read_source_handles is None or project_search_without_snippets is None:
            return self.messages
        expected: list[dict[str, Any]] = []
        group_start = -1
        group_has_read = False
        read_boundary = -1
        for index, message in enumerate(self.messages):
            role = message.get("role")
            if role == "assistant" and message.get("tool_calls"):
                if expected or not isinstance(message["tool_calls"], list):
                    return self.messages
                expected = list(message["tool_calls"])
                group_start, group_has_read = index, False
            elif role == "tool":
                if not expected:
                    return self.messages
                call = expected.pop(0)
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict) or message.get("name") != function.get("name"):
                    return self.messages
                # Ordinary retained results may omit IDs; the executor writes
                # them in declared order. Existing IDs must still match exactly.
                if "tool_call_id" in message and message["tool_call_id"] != call.get("id"):
                    return self.messages
                if message.get("name") != "web_search" and read_source_handles([message]):
                    group_has_read = True
                if not expected and group_has_read:
                    read_boundary = group_start
            elif expected:
                return self.messages
        if expected or read_boundary < 0:
            return self.messages
        # Restoration is not another read. If compaction removed the original
        # read group, retain discovery text rather than hide a newer search.
        provider = self.messages
        for index, message in enumerate(self.messages[:read_boundary]):
            if message.get("role") != "tool" or message.get("name") != "web_search":
                continue
            content = str(message.get("content") or "")
            projected = project_search_without_snippets(content)
            if projected != content:
                if provider is self.messages:
                    provider = list(self.messages)
                provider[index] = {**message, "content": projected}
        return provider

    def prepare(self, *, prepare_fn, num_ctx: int, model: str, chat_fn,
                context_profile: dict, tool_schemas: list[dict], cancel_handle,
                audit_sink, restore_source_context,
                web_closing_sources: Callable[[list[dict[str, Any]]], tuple[str, ...]] | None = None):
        from app.application.context.usage import get_context_usage

        def restore(packed, *, compacted):
            # A soft closing cue must never enter the history compactor.
            packed = [message for message in packed
                      if message.get("_msg_id") != _WEB_CLOSING_CONTEXT_ID]
            return restore_source_context(packed, compacted=compacted, max_chars=min(7000, num_ctx))

        self.messages, compacted, usage = prepare_fn(
            self.messages, num_ctx=num_ctx, model=model, chat_fn=chat_fn,
            context_profile=context_profile, tool_schemas=tool_schemas,
            cancel_handle=cancel_handle, audit_sink=audit_sink,
            pinned_message_ids=self.guidance_message_ids | {"web-source-context"},
            restore_messages=restore,
        )
        # Derive handles from the final packed payload, including any source
        # blocks that were removed after restoration by the request packer.
        pages = web_closing_sources(self.messages) if web_closing_sources is not None else ()
        candidate = self.refresh_web_closing_context(self.messages, read_pages=pages)
        if len(candidate) != len(self.messages):
            candidate_usage = get_context_usage(
                candidate, ctx_size=num_ctx,
                reserved_output_tokens=int(context_profile["reserved_output_tokens"]),
                reserved_system_tokens=int(context_profile.get("reserved_system_tokens") or 4096),
                safety_margin_tokens=int(context_profile.get("safety_margin_tokens") or 2048),
                extra_categories={"tools": list(tool_schemas)} if tool_schemas else None,
            )
            thresholds = context_profile.get("compaction_thresholds") or {}
            compact_threshold = float((thresholds.get("auto") or {}).get("percent")
                                      or (60.0 if num_ctx < 16_384 else 75.0))
            safe_input_budget = int(context_profile.get("safe_input_budget") or 0)
            # The cue is optional: it must never displace evidence or trigger
            # compaction that the actual request payload did not require.
            if float(candidate_usage["percent"]) < compact_threshold and (
                    safe_input_budget <= 0 or int(candidate_usage["current_tokens"]) <= safe_input_budget):
                self.messages, usage = candidate, candidate_usage
        return compacted, usage

    def activate_skill_result(self, *, name: str, args: dict, tool_meta: dict, status: str):
        _skill_snapshot_changed = False
        _skill_receipt = None
        if (name == "runtime_control" and str(args.get("operation") or "").strip().lower() == "skill_load"
                and status == "ok" and tool_meta.get("ok")):
            try:
                skill_result = tool_meta.get("result", {})
                _skill_snapshot_changed = self.skills.activate(
                    skill_result["skill"], skill_result.get("reason", ""),
                    refresh=True,
                )
                selected = next(item for item in self.skills.snapshots()
                                if item["name"] == skill_result["skill"]["name"])
                _skill_receipt = {key: selected[key] for key in ("name", "title", "sha256", "reason")}
                _skill_receipt.update({key: selected[key] for key in
                    ("candidate_id", "revision", "package_sha256", "directory") if key in selected})
                _skill_receipt["already_loaded"] = not _skill_snapshot_changed
                # The full instruction has one pinned owner. Tool history
                # and UI receive a receipt, not a duplicate instruction.
                tool_meta = {"ok": True, "status": "completed",
                    "result": {"skill": _skill_receipt}, "text": json.dumps({
                    "skill": _skill_receipt,
                    "message": "Skill instructions are active in the current task context.",
                }, ensure_ascii=False)}
            except (KeyError, TypeError, OSError, ValueError) as exc:
                tool_meta = {"ok": False, "error": "skill_activation_failed", "text": str(exc)}
        return tool_meta, _skill_snapshot_changed, _skill_receipt
