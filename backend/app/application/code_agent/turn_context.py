"""Initial messages and persisted planning context for the code-agent run.

Prompt/history/TaskSpec algorithms remain with their existing owners. This
module assembles their output in the established message order; it never calls
the model or decides whether a final answer is accepted.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.application.code_agent.document_validation import infer_expected_page_count
from app.application.code_agent.history import _coerce_history, is_runtime_block
from app.application.code_agent.loop_helpers import (
    _schema_tool_name, build_task_state_block, upsert_task_state_message,
)
from app.application.code_agent.planning import PlanArtifact, plan_artifact_from_dict
from app.application.code_agent.prompts import (
    _build_system_prompt, _build_turn_context, _build_project_context,
    _shell_guidance, _NO_PROJECT_BLOCK, _PROJECT_CONNECTED_BLOCK,
    _is_scratch_workspace, _scratch_workspace_root,
)
from app.application.code_agent.run_journal import past_task_hints
from app.application.code_agent.working_set import WORKING_SET_ID, FileWorkingSet
from app.application.code_agent.taskspec import (
    TaskSpec, derive_task_spec, is_continuation_message, merge_task_spec, taskspec_context,
)
from app.application.context.compaction import (
    RUNTIME_BLOCK_KEY, TASK_CONTRACT_PREFIX, TASK_CONTRACT_MARKER_VALUE, TASK_STATE_MARKER_KEY,
)
from app.application.tool_providers import ToolRegistry
from app.application.code_agent.task_guidance import (
    DELIVERY_GUIDANCE, task_guidance_blocks,
)
from app.application.code_agent.task_skills import (
    CATALOG_ID, CONTEXT_ID, SkillContext,
    catalog_context, insert_skill_context, skill_for_path,
)


logger = logging.getLogger(__name__)
PromptBuilder = Callable[..., str]
REQUEST_CONTEXT_ID = "request-context"


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
    current_message_is_runtime: bool = False,
    server_history: list[dict[str, Any]] | None = None,
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
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(server_history if server_history is not None else _coerce_history(conversation_history))
    # The current request is the owner's raw text only; its runtime context is a
    # separate block that the provider projection moves into the system message.
    request_block = ""
    if request_context:
        request_block = "[Контекст текущего запроса]\n" + request_context
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
        request_block = f"{taskspec_context(task_spec)}\n\n{request_block}"
    if request_block.strip():
        messages.append({"role": "user", "content": request_block.strip(),
                         "_msg_id": REQUEST_CONTEXT_ID, RUNTIME_BLOCK_KEY: "request_context"})
    if current_message_is_runtime:
        # A Resume slice carries a server-authored continuation instruction,
        # not the owner's words: it is a runtime block, never a user message.
        messages.append({"role": "user", "content": user_message, RUNTIME_BLOCK_KEY: "continuation"})
    else:
        messages.append({"role": "user", "content": user_message})
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
        self.working_set = FileWorkingSet(root=root, working_dir=working_dir)
        self.script_hint_sent = False

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
        # Only a new chat needs earlier tasks: an ongoing chat has them in its history,
        # and a hint that changes between its requests would break the prompt prefix.
        new_chat = not any(message.get("role") in {"user", "assistant"} and not is_runtime_block(message)
                           for message in self.messages[:-1])
        hints = self._past_task_hints() if new_chat and not resume else ""
        if self.catalog or hints:
            text = "\n\n".join(part for part in (self.catalog, hints) if part)
            self.messages = insert_skill_context(self.messages, text, CATALOG_ID)
            self.guidance_message_ids.add(CATALOG_ID)
        self.skill_reminder_pending = False
        self.skill_reminder_sent = False
        self.refresh_task_state = True
        self.awaiting_input_replies: list[str] = []
        self.user_input_policy_applied = False


    def script_skill_hint(self, *, name: str, args: dict, ok: bool) -> str:
        """One note per run when a chat writes a script outside the skills folder.

        John 2026-10-07 (option б): with no ready skill the model wrote its
        transcription script into the task folder twice out of two, although the
        guidance says to save it as a skill. Only chats without a project: in a
        connected project a script is the project's own code. Never blocks.
        """
        from app.application.code_agent import task_skills

        if self.script_hint_sent or not ok or name != "write_file" or not _is_scratch_workspace(self.root):
            return ""
        raw = str(args.get("path") or "").strip()
        if Path(raw).suffix.lower() not in {".py", ".ps1", ".bat", ".cmd", ".sh", ".js"}:
            return ""
        path = Path(raw) if Path(raw).is_absolute() else Path(self.working_dir or self.root) / raw
        try:
            path.resolve().relative_to(task_skills.SKILLS_ROOT.resolve())
            return ""
        except ValueError:
            pass
        self.script_hint_sent = True
        return (f"[Заметка Elira] Скрипт записан в папку задачи. Если он пригодится снова, сохрани его "
                f"навыком: {task_skills.SKILLS_ROOT}\\<имя>\\ (SKILL.md с описанием и этот скрипт) — тогда в новом "
                "чате он найдётся в каталоге навыков.")

    def _past_task_hints(self) -> str:
        """1-3 earlier tasks of this project, or of the chat sandbox for chats."""
        scratch = _scratch_workspace_root()
        if scratch is not None and _is_scratch_workspace(self.root):
            def same_place(raw: str) -> bool:
                path = Path(raw)
                return path == scratch or path.parent == scratch / "chats"
        else:
            key = os.path.normcase(str(self.root))

            def same_place(raw: str) -> bool:
                return os.path.normcase(raw) == key
        try:
            return past_task_hints(self.raw_user_message, same_place=same_place, exclude_run_id=self.run_id)
        except Exception as exc:  # noqa: BLE001 - a hint never blocks a run
            logger.debug("past task hints unavailable: %s", exc)
            return ""

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
                "прочитай SKILL.md подходящего навыка (read_file). Для кода обычно нужен навык языка и code-change; "
                "для поиска причины сбоя — diagnostics. Выбери сама по текущей задаче. "
                "Если подходящего навыка нет или задача не требует профильной инструкции, "
                "продолжай доступными инструментами."
            ), "elira-skill-reminder")
            self.skill_reminder_sent = True


    def add_guidance(self, *, schemas: list[dict], download_requested: bool,
                      task_instructions: str, step: int, work_started: bool = True) -> None:
        guidance = task_guidance_blocks({_schema_tool_name(schema) for schema in schemas})
        if not work_started:
            guidance.pop("work", None)
        # The web guidance block (which opens with WEB_SOURCE_FIDELITY_GUIDANCE)
        # is a pinned runtime block projected into the system section, so the
        # source-fidelity contract is not appended to the system prompt twice.
        if download_requested or "resources" in guidance:
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
            guidance_message = {"role": "user", "content": block, "_msg_id": message_id,
                                RUNTIME_BLOCK_KEY: "guidance"}
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
        # Replace in place: a stable block order keeps the projected system
        # prefix unchanged while the contract text is unchanged.
        existing = next((index for index, message in enumerate(self.messages)
                         if message.get(TASK_STATE_MARKER_KEY) == TASK_CONTRACT_MARKER_VALUE), None)
        if existing is None:
            self.messages.insert(1, contract_message)
        else:
            self.messages[existing] = contract_message
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
                audit_sink, restore_source_context):
        # No runtime closing cue follows a web read: a trailing user-role
        # "next step, do not output" block made Qwen3.8 treat its finished
        # answer as reasoning, emit </think> and write the answer twice.
        def restore(packed, *, compacted):
            packed = restore_source_context(packed, compacted=compacted, max_chars=min(7000, num_ctx))
            return self.working_set.restore(packed, compacted=compacted, num_ctx=num_ctx,
                                            read_text=self._file_text)

        self.messages, compacted, usage = prepare_fn(
            self.messages, num_ctx=num_ctx, model=model, chat_fn=chat_fn,
            context_profile=context_profile, tool_schemas=tool_schemas,
            cancel_handle=cancel_handle, audit_sink=audit_sink,
            pinned_message_ids=self.guidance_message_ids | {"web-source-context", WORKING_SET_ID},
            restore_messages=restore,
        )
        return compacted, usage

    def _file_text(self, path: Path) -> str:
        """Current numbered text of a working-set file, exactly as read_file shows it."""
        from app.application.code_agent.tools._files import tool_read_file

        result = tool_read_file(self.root, path=str(path), limit=400)
        text = str(result.get("text") or "")
        return text if result.get("ok") and not text.startswith("[") else ""

    def activate_skill_result(self, *, name: str, args: dict, tool_meta: dict, status: str):
        """Reading or editing data/skills/<name>/SKILL.md pins that instruction for the task.

        No skill operation exists: the model reads a skill with read_file, and the
        pinned copy keeps it through context compaction. The tool result itself is
        unchanged, so the model can still edit the file with its exact text.
        """
        changed, receipt = False, None
        if (name in {"read_file", "write_file", "edit_file"} and status == "ok" and tool_meta.get("ok", True)):
            path = tool_meta.get("touched_path") or args.get("path") or ""
            base = self.working_dir or self.root
            if path and base is not None and not Path(str(path)).is_absolute():
                path = str((Path(base) / str(path)).resolve())
            try:
                changed = self.skills.activate_path(path)
            except (OSError, ValueError, TypeError) as exc:
                logger.info("Skill instruction not pinned for %s: %s", path, exc)
            if changed:
                skill = skill_for_path(path)
                selected = next(item for item in self.skills.snapshots() if item["name"] == skill)
                receipt = {key: selected[key] for key in ("name", "title", "sha256", "directory")}
        return tool_meta, changed, receipt
