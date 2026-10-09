"""Code-agent runtime — single-shot run plus a streaming generator with
cancellation and multi-turn conversation support.

The streaming variant `stream_code_agent` yields events shaped for
Server-Sent Events on the API side; the legacy `run_code_agent` keeps
the synchronous dict-returning behaviour expected by existing tests.

Tools now return structured dicts (see app.application.code_agent.tools).
We extract ``text`` to feed back to the LLM and pass the rest through
to event consumers (the frontend uses ``touched_path`` / ``old_content``
/ ``new_content`` / ``diff_action`` for live IDE updates and diff
preview).
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

from app.application.code_agent.run_control import (
    _register_run,
    _cancel_handle_for,
    _unregister_run,
    request_cancel,
    submit_workflow_response,
)
from app.application.code_agent.model_turn import (
    _ANTI_REPEAT_SAMPLING,
    _normalize_reasoning_effort,
    _thinking_template_kwargs,
    _qwen_generation_options,
    _chat_events,
    _local_chat_stream,
    stream_model_turn,
    decode_response,
    recover_tool_calls,
)

from app.application.tool_providers import (
    ToolRegistry,
    build_runtime_tool_registry,
)
from app.application.code_agent.capabilities import (
    ALL_BUILTIN_TOOLS, normalize_capability_groups, file_delivery_requested,
)
from app.application.code_agent.runtime_activation import (
    RuntimeActivation, _load_runtime_activation_state,
)
from app.application.code_agent.turn_context import (
    build_initial_turn, TurnContext, SkillRestoreError, _load_planning_state, _bounded_planning_recon,
)
from app.application.code_agent.run_observations import RunObservations
from app.application.code_agent.tool_execution import (
    _exec_with_heartbeat, run_tool, run_ask_user, run_workflow_request,
    run_runtime_workflow, RUNTIME_WORKFLOW_STATUSES,
)
from app.application.code_agent.answer_acceptance import AnswerAcceptance
from app.application.code_agent.answer_media import merge_answer_media
from app.application.code_agent.answer_contracts import (
    explicit_quote_request, infer_quote_word_limit,
)
from app.application.code_agent.planning import (
    PlanArtifact, build_planning_messages, parse_plan_from_text, plan_context_block,
    planner_limits,
)
from app.application.code_agent.taskspec import taskspec_context, taskspec_report, task_spec_from_report
from app.application.code_agent.run_evidence import EvidenceKind, RunEvidence
from app.application.context.compaction import RUNTIME_BLOCK_KEY
from app.application.context.usage import get_context_usage
from app.application.projects.scope import project_scope_id
from app.application.agent_kernel.executor import (
    ToolExecutionRequest,
    execute_tool as _kernel_exec,
    workflow_approval_matches,
)
from app.application.monitoring.inference import record_inference_telemetry
# Project indexing/RAG was extracted to .indexing; re-exported here so existing
# importers (file_watcher, code_agent_routes, tests) keep importing these names
# from agent_loop unchanged.
from app.application.code_agent.indexing import (  # noqa: F401
    DEFAULT_INDEX_PATTERNS,
    index_project,
    project_corpus_status,
    recall_from_rag,
    reindex_file,
    unindex_file,
)
# Inline tool-call recovery extracted to .inline_tool_calls (used by the loop).
from app.application.code_agent.inline_tool_calls import (
    _contains_tool_trace,
    _strip_tool_call_markup,
)
from app.application.code_agent.tools._files import recover_read_path_from_glob
from app.application.code_agent.tools._meta import delegate_tool_allowed, delegate_read_schemas
# System-prompt construction extracted to .prompts; re-exported so the loop and
# tests keep importing these from agent_loop unchanged.
from app.application.code_agent.prompts import (  # noqa: F401
    _CODE_AGENT_BASE_TOOLS,
    _build_system_prompt,
    _build_turn_context,
)
from app.core.persona_defaults import DEFAULT_PROFILE
from app.core.redaction import redact_secrets
# History coercion + rolling summarization extracted to .history; it imports
# nothing from agent_loop (a leaf), so re-exporting here keeps existing importers
# (code_agent_routes, tests) and the loop's `summarize_fn=summarize_history`
# resolving with no import cycle. DEFAULT_MODEL lives there because
# summarize_history binds it as a default-arg value.
from app.application.code_agent.history import (  # noqa: F401
    DEFAULT_MODEL,
    _coerce_history,
    _local_chat,
    _resolve_code_route,
    project_runtime_roles,
    summarize_history,
)
# Project-prompt CRUD extracted to .project_prompt; a leaf. Re-exported (with
# PROJECT_PROMPT_FILENAME) so code_agent_routes and tests keep importing these
# from agent_loop unchanged.
from app.application.code_agent.project_prompt import (  # noqa: F401
    get_project_prompt,
    init_project_prompt,
    set_project_prompt,
)

logger = logging.getLogger(__name__)

# `_runtime_*` arguments are injected by this loop only (resource binding,
# refusal reasons, the verified BOM snapshot). A model or prompt-injected page
# must not supply them, and they are not part of any published event.
_RUNTIME_ARGUMENT_PREFIX = "_runtime_"


def _without_runtime_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in arguments.items() if not str(key).startswith(_RUNTIME_ARGUMENT_PREFIX)}


def _public_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return redact_secrets(_without_runtime_arguments(arguments))


def _run_owned_servers(run_id: str) -> list[dict]:
    """R2: alive servers this run started (module-level so tests patch it)."""
    try:
        from app.application.code_agent.tools._run import run_owned_servers
        return run_owned_servers(run_id)
    except Exception:
        return []


# Text/format, Workflow-request, context-window, RAG and telemetry helpers
# were extracted to .loop_helpers (a leaf — imports nothing from agent_loop), and
# re-exported here so existing importers (tests, file_watcher) and the core loop
# below keep resolving these names from agent_loop unchanged. Because the core
# loop calls every one of them through this module's namespace, tests that
# `patch("...agent_loop.<name>")` still take effect.
from app.application.code_agent.loop_helpers import (  # noqa: F401
    ContextBudgetError,
    TOOL_RESULT_LLM_LIMIT,
    _ASK_USER_SCHEMA,
    _WORKFLOW_REQUEST_SCHEMA,
    _fact_from_tool,
    _facts_digest,
    _recent_tool_snippet,
    _recent_tools_digest,
    session_cancel_requested,
    take_session_inputs,
    tool_state_changed,
    _flatten_for_summary,
    _messages_char_count,
    _prepare_messages_for_llm,
    _record_code_route_metric,
    _schema_tool_name,
    _short_arg_hint,
    _truncate,
    _truncate_for_llm,
    _try_remember_turn,
    task_persistence_policy,
    persistence_policy_from_state,
    run_persistence_policy,
    task_memory_write_allowed,
)


def _stream_code_agent_core(
    *,
    user_message: str,
    memory_query: str | None = None,
    task_instructions: str = "",
    project_root: Path | str,
    working_dir: Path | str | None = None,
    model: str = "auto",
    agent_id: str = "code-agent",
    conversation_history: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    num_ctx: int | None = None,
    base_tools: tuple[str, ...] | list[str] | None = None,
    auto_remember: bool = True,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
    chat_stream_fn: Callable[..., Any] | None = None,
    compaction_audit_sink: Callable[[dict[str, Any]], None] | None = None,
    profile_name: str = "Инженерный",
    permission_mode: str = "ask",
    thinking: bool = False,
    reasoning_effort: str | None = None,
    resume: bool = False,
    pause_for_workflow_request: bool = False,
    workflow_approval: dict[str, Any] | None = None,
    resource_refs: list[dict[str, Any]] | None = None,
    initial_sources: list[dict[str, Any]] | None = None,
    read_only: bool = False,
    _server_history: list[dict[str, Any]] | None = None,
    _history_checkpoint: Callable[..., None] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream the agent loop as events.

    Reasoning and ordinary drafts are emitted live. Source-first Web answers
    defer content until composition; only final_response accepts an answer.

    Yields dicts with a `type` discriminator:
      - {"type": "run_started", "run_id": ...}
      - {"type": "step_started", "step": N}
      - {"type": "tool_started", "step": N, "tool": str, "arguments": dict}
      - {"type": "tool_call", "step": N, "tool": str, "arguments": dict,
         "result": str, "touched_path"?: str,
         "old_content"?: str, "new_content"?: str, "diff_action"?: str}
      - {"type": "final_response", "step": N, "text": str,
         "answer_status": "complete" | "degraded" | "needs_input"}
      - {"type": "done", "ok": bool, "steps": int, "stop_reason": str,
         "error": str | None}
    """
    profile_name = DEFAULT_PROFILE
    selected_reasoning_effort = _normalize_reasoning_effort(
        reasoning_effort,
        thinking=thinking,
    )
    thinking = selected_reasoning_effort != "none"
    root = Path(project_root).resolve()
    scope_id = project_scope_id(root)
    rid = run_id or uuid.uuid4().hex
    effective_agent_id = str(agent_id or "code-agent").strip() or "code-agent"
    pending_workflow_approval = (
        dict(workflow_approval) if isinstance(workflow_approval, dict) else {}
    )
    cancel_event = _register_run(rid, resume=resume)
    upstream_cancel_handle = _cancel_handle_for(cancel_event)
    # Delivery: a session-level Stop may land while NO slice is registered
    # (between slices / during continuation build), where request_cancel finds
    # no live run. Make it visible to THIS slice the moment it registers — the
    # loop's own cancel checks then terminate before any model or tool call.
    if session_cancel_requested(rid):
        cancel_event.set()
    try:
        if not root.exists() or not root.is_dir():
            yield {"type": "run_started", "run_id": rid}
            yield {
                "type": "done",
                "ok": False,
                "steps": 0,
                "stop_reason": "error",
                "error": f"project_root does not exist or is not a directory: {project_root}",
            }
            return

        model, offline_ctx, _route_decision = _resolve_code_route(
            model,
            num_ctx,
            agent_id=effective_agent_id,
        )
        from app.application.context.profile import (
            ContextResolutionError,
            resolve_context_window,
        )

        try:
            context_profile = resolve_context_window(
                offline_ctx or None,
                model=model,
                thinking=thinking,
                live=chat_fn is None,
                fresh=True,
            )
        except ContextResolutionError as exc:
            yield {"type": "run_started", "run_id": rid}
            yield {
                "type": "done",
                "ok": False,
                "steps": 0,
                "stop_reason": "error",
                "error": str(exc),
            }
            return
        safe_num_ctx = int(context_profile["ctx_size"])
        _record_code_route_metric(rid, _route_decision, safe_num_ctx, agent_id=effective_agent_id)
        raw_user_message = user_message if memory_query is None else memory_query
        activation = RuntimeActivation.for_run(
            root, rid, base_tools, restored_state=_load_runtime_activation_state(rid),
            registry_builder=build_runtime_tool_registry,
        )
        # The public entrypoints preserve raw user text in memory_query.
        # Library/attachment contents are evidence, not a file-delivery request.
        download_requested = file_delivery_requested(
            user_message if memory_query is None else memory_query,
        )

        # Aggregate the default work tools into one registry. The agent loop only
        # talks to the registry from here on.
        #   - BuiltinToolProvider exposes discovery on every request.
        #   - SSH/IT Ops schemas appear after capability_load of their group
        #     in this run.
        #   - MCP/LSP schemas appear only for servers explicitly started by this
        #     run; other globally running integrations remain hidden.
        # The HTTP app seeds these at startup, but the runtime is also called
        # directly by tests and CLI integrations. Reuse the same idempotent
        # seeder so ToolExecutor never sees an unregistered built-in spec.
        from app.application.tool_registry.runtime import seed_builtin_tools

        seed_builtin_tools()
        schema_update = activation.rebuild()
        registry, all_schemas = schema_update.registry, schema_update.schemas
        # Ordinary work tools are visible from the first step. Specialist
        # built-ins and integrations appear after explicit model calls.
        chat = chat_fn or _local_chat
        stream_chat = chat_stream_fn
        if chat_fn is None and stream_chat is None:
            stream_chat = _local_chat_stream

        initial_turn = build_initial_turn(
            root=root, working_dir=working_dir, user_message=user_message,
            raw_user_message=raw_user_message, memory_query=memory_query,
            model=model, profile_name=profile_name, conversation_history=conversation_history,
            resource_refs=resource_refs, active_schemas=all_schemas,
            system_prompt_builder=_build_system_prompt, turn_context_builder=_build_turn_context,
            current_message_is_runtime=resume,
            server_history=_server_history,
        )
        task_spec, task_spec_source = initial_turn.task_spec, initial_turn.task_spec_source
        document_page_count_contract = initial_turn.document_page_count_contract
        turn_context = TurnContext(messages=initial_turn.messages, raw_user_message=raw_user_message,
                                   root=root, working_dir=working_dir, run_id=rid)
        # Bind the completion contract to THIS run's spec/source so every terminal
        # done event carries uniform task-state (FIX-1/8).
        def _completion_fields(crit, *, terminated_incomplete: bool = False) -> dict:
            cs = crit.completion_status()
            return {
                "completion_status": cs,
                "criteria": crit.report(),
                "criteria_confirmed": cs == "confirmed",  # compat mirror only (FIX-6)
                "partial": terminated_incomplete or cs in ("partial", "unverified", "failed"),
                "task_spec": taskspec_report(task_spec) if task_spec else None,
                "task_spec_source": task_spec_source,
            }

        def _incomplete_model_output(*, step: int, error: str, error_code: str) -> dict:
            return {
                "type": "done", "ok": False, "steps": step,
                "stop_reason": "error", "answer_status": "degraded",
                "error": error, "error_code": error_code, "resumable": True,
                **_completion_fields(criteria, terminated_incomplete=True),
                "completion_status": "partial", "criteria_confirmed": False,
            }
        yield {
            "type": "run_started",
            "run_id": rid,
            "profile_name": profile_name,
            "ui_profile_name": "Elira / Auto",
            "runtime_activation": activation.snapshot(),
        }
        yield {
            "type": "context_resolved",
            "step": 0,
            "requested_context_mode": context_profile.get("requested_context_mode"),
            "requested_context_cap": context_profile.get("requested_context_cap"),
            "server_context_window": context_profile.get("server_context_window"),
            "effective_context_window": context_profile.get("effective_context_window"),
            "limiting_source": context_profile.get("limiting_source"),
            "context_profile_source": context_profile.get("context_profile_source"),
            "reserved_output_tokens": context_profile.get("reserved_output_tokens"),
            "reserved_system_tokens": context_profile.get("reserved_system_tokens"),
            "safety_margin_tokens": context_profile.get("safety_margin_tokens"),
            "safe_input_budget": context_profile.get("safe_input_budget"),
            "compaction_thresholds": context_profile.get("compaction_thresholds"),
            "thinking": bool(thinking),
            "reasoning_effort": selected_reasoning_effort,
        }

        tool_round_trips = 0
        compaction_count = 0
        call_log: list[str] = []
        # Grounding across turns: compact facts the discovery tools revealed this
        # run, handed back next turn as an authoritative context block so the
        # model grounds instead of confabulating (see loop_helpers._fact_from_tool
        # and the FACTS_PREFIX block in the frontend history builder).
        established_facts: list[str] = []
        # Verbatim buffer of recent grounding-tool outputs (last few, in full-ish),
        # carried into the next turn alongside the compact facts digest.
        recent_tool_outputs: list[str] = []
        # One run-local evidence ledger owns mutation, verification, artifact,
        # remote-observation and external-source truth. A mutation advances its
        # project epoch, making older verification receipts stale.
        _last_failure: dict[str, str] = {}  # for the deterministic stop summary
        _failure_counts: dict[str, int] = {}
        acceptance = AnswerAcceptance()
        # Web research path (John, 2026-10-05): read 2 sites → enough? answer :
        # read more, up to the site limit → then the model always answers from
        # what it read. Non-empty = the next turn is an answer turn without tools.
        _last_glob_matches: tuple[str, ...] = ()
        _read_file_failures: dict[str, int] = {}
        pending_redirected_jobs: set[int] = set()

        # TaskSpec per-criterion state (Ph7.4/7.5): DONE is decided by verifiers,
        # not the model's word. Each criterion is unconfirmed → confirmed (a matching
        # verifier passed) / failed (matching verifier red). completion_status is a
        # deterministic function of this — kept SEPARATE from runtime `ok`. We never
        # burn an extra LLM turn to nag; unconfirmed criteria are reported at finalize.
        durable_state: dict[str, Any] = {}
        try:
            from app.application.code_agent.run_journal import RunJournal

            durable_state = RunJournal.load(rid).state
        except Exception:
            durable_state = {}
        if resume and isinstance(durable_state.get("task_spec"), dict):
            task_spec = task_spec_from_report(durable_state["task_spec"])
            task_spec_source = "run_journal"
        if resume:
            request = durable_state.get("request") or {}
            turn_context.original_goal = str(request.get("memory_query") or request.get("user_message") or raw_user_message)
            turn_context.clarifications = list((durable_state.get("task_outcome") or {}).get("contract", {}).get("clarifications", []))
        observations = RunObservations(task_spec=task_spec, durable_state=durable_state,
                                       resume=resume, initial_sources=initial_sources)
        run_evidence, criteria = observations.evidence, observations.criteria
        if resume:
            acceptance.web_source_correction_sent = durable_state.get("web_source_correction_sent") is True
        task_outcome, command_progress = observations.outcome, observations.commands
        if not task_outcome.contract.get("goal"):
            task_outcome.set_contract(turn_context.original_goal, task_outcome.contract.get("requirements", []))
        mutated_files, verification_log = observations.mutated_files, observations.verifications
        durable_failures = observations.failed_attempts
        quote_word_limit = infer_quote_word_limit(raw_user_message)
        saved_quote_word_limit = durable_state.get("quote_word_limit")
        if resume and type(saved_quote_word_limit) is int and 0 < saved_quote_word_limit < 10_000:
            quote_word_limit = saved_quote_word_limit
        acceptance.quote_correction_sent = bool(durable_state.get("quote_word_limit_correction_sent"))
        acceptance.quote_source_correction_sent = bool(durable_state.get("quote_source_correction_sent"))
        acceptance.cadence_correction_sent = bool(durable_state.get("web_cadence_correction_sent"))
        # ── Structured planning preflight ────────────────────────────────────
        # On a structured task the selected reasoning mode is used for a planning
        # call and then remains active for every execution/verification model call.
        # Only the physical context/output window bounds the plan artifact.
        # The plan is durable in the RunJournal (state["plan"]): on Resume /
        # auto-continuation the existing plan is REUSED and the planner never
        # runs a second time. request.thinking (the user setting) is never
        # rewritten; the actually-applied mode is journalled separately.
        plan: PlanArtifact | None = None
        applied_thinking_mode = "raw" if thinking else "off"
        _existing_plan, _planned_before = _load_planning_state(rid)
        if _existing_plan is not None:
            plan = _existing_plan
            applied_thinking_mode = "plan_reused"
        elif _planned_before:
            # Planner already ran once (fallback, no stored plan): do NOT re-plan.
            applied_thinking_mode = "planning_fallback"
        elif thinking and task_spec is not None:
            if cancel_event.is_set():
                yield {
                    "type": "done", "ok": False, "steps": 0,
                    "stop_reason": "cancelled", "error": "Cancelled by user",
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return
            yield {"type": "planning_started", "run_id": rid}
            _planner_max_tokens, _planner_project_chars = planner_limits(context_profile)
            _project_ctx = _bounded_planning_recon(
                registry, char_cap=_planner_project_chars,
            )
            _planner_response: dict[str, Any] = {}
            _planner_failed = False
            try:
                _planner_kwargs = {
                    "model": model,
                    "messages": build_planning_messages(
                        taskspec_context=taskspec_context(task_spec),
                        project_context=_project_ctx,
                        project_char_cap=_planner_project_chars,
                    ),
                    # No tools by construction. The per-call output cap is
                    # enforced by the OpenAI-compatible provider.
                    "options": {
                        "num_ctx": safe_num_ctx,
                        "active_context_limit": safe_num_ctx,
                        "max_tokens": _planner_max_tokens,
                        **_qwen_generation_options(selected_reasoning_effort),
                    },
                }
                for _planner_event in _chat_events(
                    chat_fn=chat,
                    chat_stream_fn=stream_chat,
                    kwargs=_planner_kwargs,
                    cancel_event=cancel_event,
                    cancel_handle=upstream_cancel_handle,
                ):
                    if cancel_event.is_set():
                        break
                    if _planner_event["type"] == "heartbeat":
                        yield {"type": "heartbeat", "phase": "planning"}
                    elif _planner_event["type"] == "response":
                        _planner_response = dict(_planner_event["value"] or {})
                    # Reasoning/delta output is intentionally not surfaced or
                    # retained: only the validated PlanArtifact crosses phases.
            except Exception:
                _planner_failed = True
            if not _planner_failed:
                _planner_content = str(
                    ((_planner_response.get("message") or {}).get("content") or "")
                )
                plan = parse_plan_from_text(_planner_content)
            if cancel_event.is_set():
                yield {
                    "type": "done", "ok": False, "steps": 0,
                    "stop_reason": "cancelled", "error": "Cancelled by user",
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return
            if plan is None or not plan.is_valid():
                plan = None
                applied_thinking_mode = "planning_fallback"
                yield {
                    "type": "planning_fallback", "run_id": rid,
                    "reason": "planner produced no valid PlanArtifact",
                }
            else:
                applied_thinking_mode = "planning_then_execution"
                yield {"type": "plan_ready", "run_id": rid, "plan": plan.to_dict()}

        # The planning LLM can select optional schema groups before the first
        # execution turn.  This keeps the execution prompt prefix stable instead
        # of loading (for example) browser tools after tens of thousands of tokens
        # and forcing an expensive cold prefill.  It changes visibility only;
        # execution still goes through the canonical registry/executor.
        if plan is not None:
            planned_groups = set(normalize_capability_groups(plan.capability_groups))
            new_groups = planned_groups - activation.capability_groups
            if new_groups:
                activation.activate_groups(new_groups)
                schema_update = activation.rebuild()
                registry, all_schemas = schema_update.registry, schema_update.schemas
                yield {
                    "type": "runtime_activation_changed",
                    "run_id": rid,
                    "source": "planner",
                    "runtime_activation": activation.snapshot(),
                }

        # Structural event ONLY when a planning preflight actually engaged. A
        # direct/simple run emits nothing new and retains the one-call path.
        if applied_thinking_mode in (
            "planning_then_execution", "planning_fallback", "plan_reused",
        ):
            yield {
                "type": "phase_changed", "run_id": rid, "phase": "execution",
                "applied_thinking_mode": applied_thinking_mode,
            }
        _brain_phase_tracking = applied_thinking_mode in (
            "planning_then_execution", "plan_reused",
        )
        _verification_phase_emitted = False

        def _enter_verification_phase() -> dict[str, Any] | None:
            nonlocal _verification_phase_emitted
            if not _brain_phase_tracking or _verification_phase_emitted:
                return None
            _verification_phase_emitted = True
            return {
                "type": "phase_changed", "run_id": rid, "phase": "verification",
                "applied_thinking_mode": applied_thinking_mode,
            }

        if plan is not None:
            # The plan is model-authored context: a runtime block (projected into
            # the system section as data), never a message in the owner's name.
            turn_context.messages.append({"role": "user", "content": plan_context_block(plan),
                                          RUNTIME_BLOCK_KEY: "plan"})

        step = 0
        try:
            turn_context.initialize_skills(resume=resume)
        except SkillRestoreError as exc:
            yield {"type": "done", "ok": False, "steps": 0, "stop_reason": "error",
                   "error_code": "skill_restore_failed", "error": str(exc), "resumable": False}
            return

        while True:
            step += 1
            if cancel_event.is_set():
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "cancelled",
                    "error": "Cancelled by user",
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return

            yield {"type": "step_started", "step": step}
            pending_inputs = take_session_inputs(rid)
            if pending_inputs:
                for row in pending_inputs:
                    observations.apply_user_clarification(row["text"], root=root)
                task_spec = observations.task_spec
                for event in turn_context.apply_user_inputs(pending_inputs, step=step):
                    yield {**event, "task_spec": taskspec_report(task_spec) if task_spec else None,
                           "task_outcome": task_outcome.snapshot(), "code_input_epoch": observations.code_input_epoch}

            turn_context.add_skill_reminder()

            checklist_items: list[dict] = []
            try:
                if task_spec is not None:
                    from app.application.task_planner.service import list_checklist

                    checklist_items = list(
                        (list_checklist(rid) or {}).get("items") or []
                    )
            except Exception:
                checklist_items = []
            turn_context.update_task_state(
                task_spec=task_spec, criteria_rows=criteria.report(), checklist_items=checklist_items,
                mutated_files=mutated_files, verifications=verification_log,
                failed_attempts=[*durable_failures, *(call for call in call_log if call.endswith("error"))],
            )

            # Context accounting must include the exact JSON schemas sent this
            # step. Previously the UI could report ~11% while MCP schemas filled
            # almost the complete 128K server window.
            step_schemas = delegate_read_schemas(all_schemas) if read_only else list(all_schemas)
            if not read_only:
                step_schemas.append(_ASK_USER_SCHEMA)
                if activation.has_optional_tools:
                    step_schemas.append(_WORKFLOW_REQUEST_SCHEMA)
            command_progress.observe("", {}, {}, epoch=observations.progress_epoch())
            turn_context.add_guidance(schemas=step_schemas, download_requested=download_requested,
                                      task_instructions=task_instructions, step=step,
                                      work_started=bool(task_outcome.sources or task_outcome.artifact_contract_seen
                                          or run_evidence.has_mutations or turn_context.skill_reminder_pending))
            if task_outcome.refresh_deliveries():
                yield {"type": "task_outcome_changed", "step": step,
                       "task_outcome": task_outcome.snapshot()}
            outcome_context = task_outcome.context(observations.code_input_epoch)
            if outcome_context:
                turn_context.pin_outcome_context(outcome_context)
            turn_context.pin_recovery_context(command_progress.context())
            try:
                _compacted, context_usage = turn_context.prepare(
                    prepare_fn=_prepare_messages_for_llm, num_ctx=safe_num_ctx, model=model,
                    chat_fn=chat, context_profile=context_profile, tool_schemas=step_schemas,
                    cancel_handle=upstream_cancel_handle, audit_sink=compaction_audit_sink,
                    restore_source_context=run_evidence.restore_source_context,
                )
                provider_messages = turn_context.messages
                run_evidence.mark_sources_presented(provider_messages)
                # The owner's UI text alone stays in the user role; every runtime
                # block moves into the single system message (decision 2026-10-06).
                provider_messages = project_runtime_roles(provider_messages)
                if provider_messages is not turn_context.messages:
                    context_usage = get_context_usage(
                        provider_messages, ctx_size=safe_num_ctx,
                        reserved_output_tokens=int(context_profile["reserved_output_tokens"]),
                        reserved_system_tokens=int(context_profile.get("reserved_system_tokens") or 4096),
                        safety_margin_tokens=int(context_profile.get("safety_margin_tokens") or 2048),
                        extra_categories={"tools": list(step_schemas)} if step_schemas else None,
                    )
                    critical_threshold = float(
                        ((context_profile.get("compaction_thresholds") or {}).get("critical") or {}).get("percent")
                        or 95.0
                    )
                    safe_input_budget = int(context_profile.get("safe_input_budget") or 0)
                    if float(context_usage["percent"]) >= critical_threshold or (
                        safe_input_budget > 0 and int(context_usage["current_tokens"]) > safe_input_budget
                    ):
                        raise ContextBudgetError(
                            "Контекст составления ответа превышает доступное окно модели.",
                            usage=context_usage,
                        )
            except ContextBudgetError as exc:
                if cancel_event.is_set():
                    yield {
                        "type": "done",
                        "ok": False,
                        "steps": step - 1,
                        "stop_reason": "cancelled",
                        "error": "Cancelled by user",
                        **_completion_fields(criteria, terminated_incomplete=True),
                    }
                    return
                if exc.usage is not None:
                    yield {
                        "type": "context_prepared",
                        "step": step,
                        "context": exc.usage,
                    }
                # One fresh slice can remove recent output. If that slice still
                # cannot make its first call, repeating it cannot make progress.
                terminal_budget = exc.fixed_payload_exceeds_budget or (resume and step == 1)
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "error" if terminal_budget else "context_limit",
                    "resumable": not terminal_budget,
                    "error_code": "context_budget_exceeded",
                    "error": str(exc),
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return
            if cancel_event.is_set():
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "cancelled",
                    "error": "Cancelled by user",
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return
            if _compacted:
                compaction_count += 1
                # Compaction creates a new prompt prefix anyway. Refresh the
                # task snapshot on the next step, then keep it stable again.
                turn_context.refresh_task_state = True
                from app.application.context.compaction import extract_rolling_summary

                rolling_summary = extract_rolling_summary(turn_context.messages)
                yield {
                    "type": "context_compacted",
                    "step": step,
                    "context": context_usage,
                    "rolling_summary": rolling_summary or None,
                }
            yield {
                "type": "context_prepared",
                "step": step,
                "context": context_usage,
            }

            # Schemas remain compact, but visibility is not an execution gate.
            # Inline recovery may recognize any implemented built-in the model
            # already knows; integrations still require a live owning provider.
            _inline_tool_names = {
                name for schema in step_schemas
                if (name := _schema_tool_name(schema))
            } | set(ALL_BUILTIN_TOOLS)
            schema_chars = (
                len(json.dumps(step_schemas, ensure_ascii=False, separators=(",", ":")))
                if step_schemas
                else 0
            )
            llm_prompt_chars = _messages_char_count(provider_messages) + schema_chars
            run_evidence.mark_sources_presented(provider_messages)
            if run_evidence.sources:
                yield {"type": "source_evidence", "step": step, "sources": run_evidence.sources}
            llm_start = time.monotonic()
            try:
                # Always send an explicit per-request mode. Reasoning-capable
                # server profiles default to ON, so omitting kwargs when the chip
                # is off would silently re-enable thinking after a profile switch.
                active_reasoning_effort = selected_reasoning_effort
                llm_options: dict[str, Any] = {
                    "num_ctx": safe_num_ctx,
                    "active_context_limit": safe_num_ctx,
                    **_qwen_generation_options(active_reasoning_effort),
                }
                llm_kwargs = {
                    "model": model,
                    "messages": provider_messages,
                    "tools": step_schemas,
                    "options": llm_options,
                }
                response = yield from stream_model_turn(
                    chat=chat, stream_chat=stream_chat, kwargs=llm_kwargs,
                    cancel_event=cancel_event, upstream_cancel_handle=upstream_cancel_handle,
                    step=step,
                )
            except Exception as exc:
                if cancel_event.is_set():
                    yield {
                        "type": "done",
                        "ok": False,
                        "steps": step,
                        "stop_reason": "cancelled",
                        "error": "Cancelled by user",
                        **_completion_fields(criteria, terminated_incomplete=True),
                    }
                    return
                llm_duration_ms = int((time.monotonic() - llm_start) * 1000)
                record_inference_telemetry(
                    agent_id=effective_agent_id,
                    run_id=rid,
                    route=str(getattr(_route_decision, "route", "") or "code"),
                    model=model,
                    provider=str(getattr(_route_decision, "provider", "") or ""),
                    profile_id=str(getattr(_route_decision, "profile_id", "") or ""),
                    role=str(getattr(_route_decision, "role", "") or ""),
                    routing_source=str(getattr(_route_decision, "source", "") or ""),
                    requested_model=str(getattr(_route_decision, "requested_model", "") or ""),
                    num_ctx=safe_num_ctx,
                    ok=False,
                    duration_ms=llm_duration_ms,
                    streaming=stream_chat is not None,
                    prompt_chars=llm_prompt_chars,
                    tool_round_trips=tool_round_trips,
                    compaction_count=compaction_count,
                    fallback_count=1 if getattr(_route_decision, "fallback_reason", None) else 0,
                    error_category="llm_error",
                )
                logger.exception("LLM chat failed at step %d", step)
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "error",
                    "error": str(exc),
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return

            llm_duration_ms = int((time.monotonic() - llm_start) * 1000)
            if cancel_event.is_set():
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step,
                    "stop_reason": "cancelled",
                    "error": "Cancelled by user",
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return

            # Safety net: reasoning/content separation relies on the SERVER's
            # template parsing. If that ever breaks (model swap, llama.cpp
            # update), raw <think> blocks would flow into content → history →
            # the model keeps reasoning as content. Strip them client-side too.
            content, tool_calls, step_usage = decode_response(response)
            record_inference_telemetry(
                agent_id=effective_agent_id,
                run_id=rid,
                route=str(getattr(_route_decision, "route", "") or "code"),
                model=model,
                provider=str(getattr(_route_decision, "provider", "") or ""),
                profile_id=str(getattr(_route_decision, "profile_id", "") or ""),
                role=str(getattr(_route_decision, "role", "") or ""),
                routing_source=str(getattr(_route_decision, "source", "") or ""),
                requested_model=str(getattr(_route_decision, "requested_model", "") or ""),
                num_ctx=safe_num_ctx,
                ok=True,
                duration_ms=llm_duration_ms,
                streaming=stream_chat is not None,
                usage=step_usage,
                prompt_chars=llm_prompt_chars,
                completion_chars=len(content),
                tool_round_trips=tool_round_trips,
                compaction_count=compaction_count,
                fallback_count=1 if getattr(_route_decision, "fallback_reason", None) else 0,
            )
            # Live token usage for the frontend context meter / tok-s readout.
            # Reuses extract_llm_usage (the telemetry source); not a second path.
            yield {
                "type": "usage",
                "step": step,
                "prompt_tokens": int(step_usage.get("prompt_tokens") or 0),
                "cached_prompt_tokens": int(step_usage.get("cached_prompt_tokens") or 0),
                "cache_hit_ratio": round(
                    float(step_usage.get("prompt_cache_hit_ratio") or 0.0), 4
                ),
                "completion_tokens": int(step_usage.get("completion_tokens") or 0),
                "total_tokens": int(step_usage.get("total_tokens") or 0),
                "prompt_tokens_per_second": round(
                    float(step_usage.get("prompt_tokens_per_second") or 0.0), 1
                ),
                "tokens_per_second": round(float(step_usage.get("tokens_per_second") or 0.0), 1),
                "ttft_ms": int(step_usage.get("ttft_ms") or 0),
                "context": context_usage,
                "profile": context_profile,
            }

            if response.get("finish_reason") == "length":
                # Even valid-looking arguments may be a truncated proposal.
                # Keep this turn's visible draft; never recover or dispatch it.
                if stream_chat is None and content and not _contains_tool_trace(content):
                    yield {"type": "delta", "step": step, "text": content, "answer_state": "draft"}
                yield _incomplete_model_output(
                    step=step, error_code="model_output_truncated",
                    error="Модель исчерпала лимит генерации; ответ не завершён. Запуск можно продолжить.",
                )
                return

            # Some local tool-calling models emit tool calls as JSON in
            # content instead of structured tool_calls. Recover them so the
            # loop still works.
            content, tool_calls = recover_tool_calls(content, tool_calls, _inline_tool_names)

            # Reconsider a generated proposal before starting any of its tools.
            # Existing tool batches finish normally; no second executor or
            # concurrent model response is started for a user update.
            pending_inputs = take_session_inputs(rid)
            if pending_inputs:
                for row in pending_inputs:
                    observations.apply_user_clarification(row["text"], root=root)
                task_spec = observations.task_spec
                for event in turn_context.apply_user_inputs(pending_inputs, step=step):
                    yield {**event, "task_spec": taskspec_report(task_spec) if task_spec else None,
                           "task_outcome": task_outcome.snapshot(), "code_input_epoch": observations.code_input_epoch}
                continue
            if turn_context.awaiting_input_replies and content:
                reply = content.split("\n\n", 1)[0][:600]
                yield {"type": "user_input_reply", "step": step, "run_id": rid,
                       "request_ids": list(turn_context.awaiting_input_replies), "text": reply}
                turn_context.awaiting_input_replies.clear()

            if not tool_calls and _contains_tool_trace(content):
                turn_context.messages.append({
                    "role": "user",
                    RUNTIME_BLOCK_KEY: "tool_trace_correction",
                    "content": (
                        "[internal correction] Tool trace was malformed or unavailable. "
                        "Do not expose internal tool markup. Use a currently available "
                        "structured tool call or answer plainly."
                    ),
                })
                call_log.append("repaired malformed internal tool trace")
                continue

            if not tool_calls:
                final_text = _strip_tool_call_markup(content)
                if not final_text.strip():
                    yield _incomplete_model_output(
                        step=step, error_code="empty_model_response",
                        error="Модель не вернула содержательный ответ. Запуск можно продолжить.",
                    )
                    return
                answer_decision = acceptance.evaluate(
                    final_text=final_text, raw_user_message=turn_context.raw_user_message,
                    pending_redirected_jobs=pending_redirected_jobs,
                    active_capability_groups=activation.capability_groups,
                    task_outcome=task_outcome, run_evidence=run_evidence,
                    code_input_epoch=observations.code_input_epoch,
                    criteria_rows=criteria.report(),
                    persistence_policy=run_persistence_policy(rid),
                    quote_word_limit=quote_word_limit, durable_task=str(durable_state.get("task") or ""),
                    step=step, run_id=rid,
                )
                if answer_decision.action == "retry":
                    if answer_decision.activate_groups:
                        activation.activate_groups(answer_decision.activate_groups)
                        schema_update = activation.rebuild()
                        registry, all_schemas = schema_update.registry, schema_update.schemas
                    turn_context.messages.extend(answer_decision.messages)
                    acceptance.commit(answer_decision)
                    if answer_decision.log_note is not None:
                        call_log.append(answer_decision.log_note)
                    if answer_decision.event is not None:
                        correction_event = dict(answer_decision.event)
                        if answer_decision.event_task_outcome:
                            correction_event["task_outcome"] = task_outcome.snapshot()
                        if answer_decision.event_runtime_activation:
                            correction_event["runtime_activation"] = activation.snapshot()
                        yield correction_event
                    continue
                final_text, answer_status = answer_decision.text, answer_decision.answer_status
                criteria.finalize_conditionals()
                # Background processes are reported, never auto-stopped on a healthy
                # final answer. Explicit run_server(stop) or Workflow Stop owns
                # termination.
                try:
                    _alive = _run_owned_servers(rid)
                    if _alive:
                        # Background runtimes are not auto-stopped at finalization.
                        # They stop only through run_server(action='stop') or Workflow Stop.
                        _srv = "; ".join(
                            f"{s.get('kind', 'server')} pid={s['pid']}"
                            + (f" — {s['url']}" if s.get("url") else "")
                            for s in _alive)
                        final_text = final_text.rstrip() + (
                            f"\n\n[Фоновые процессы оставлены работать: {_srv}. "
                            "Остановить: run_server(action='stop', pid=…) или кнопкой Stop.]"
                        )
                except Exception:
                    pass
                _facts = _facts_digest(established_facts)
                _recent_digest = _recent_tools_digest(recent_tool_outputs)
                citations = run_evidence.citations(final_text, skip_names=not (
                    quote_word_limit is not None or explicit_quote_request(raw_user_message)))
                source_status = (
                    "unresolved" if any(item["status"] != "matched" for item in citations)
                    else "matched" if citations else "none"
                )
                pending_inputs = take_session_inputs(rid, finishing=True)
                if pending_inputs:
                    for row in pending_inputs:
                        observations.apply_user_clarification(row["text"], root=root)
                    task_spec = observations.task_spec
                    for event in turn_context.apply_user_inputs(pending_inputs, step=step):
                        yield {**event, "task_spec": taskspec_report(task_spec) if task_spec else None,
                               "task_outcome": task_outcome.snapshot(), "code_input_epoch": observations.code_input_epoch}
                    continue
                final_message = {"role": "assistant", "content": final_text}
                final_reasoning = (response.get("message") or {}).get("reasoning_content")
                if isinstance(final_reasoning, str) and final_reasoning:
                    final_message["reasoning_content"] = final_reasoning
                if _history_checkpoint:
                    _history_checkpoint([*turn_context.messages, final_message], final_text=final_text)
                yield {
                    "type": "final_response", "step": step, "text": final_text,
                    "answer_state": "accepted",
                    "answer_status": answer_status,
                    "established_facts": _facts,
                    "recent_tool_output": _recent_digest,
                    "sources": run_evidence.sources, "citations": citations,
                    "source_status": source_status,
                    "task_outcome": task_outcome.snapshot(),
                }
                if auto_remember:
                    _try_remember_turn(
                        user_message=user_message,
                        response_text=final_text,
                        project_root=root,
                        persistence_policy=run_persistence_policy(rid),
                        verified=run_evidence.has_current_passing_verification,
                        mutation_targets=(
                            receipt.target
                            for receipt in run_evidence.receipts_of_kind(EvidenceKind.MUTATION)
                        ),
                        verification_targets=(
                            receipt.target
                            for receipt in run_evidence.receipts_of_kind(EvidenceKind.VERIFICATION)
                            if receipt.passed
                            and receipt.project_epoch == run_evidence.project_epoch
                        ),
                    )
                yield {
                    "type": "done",
                    "ok": True,  # runtime health — NOT "task solved"; see completion_status
                    "steps": step,
                    "stop_reason": "answer",
                    "answer_status": answer_status,
                    "error": None,
                    "established_facts": _facts,
                    "recent_tool_output": _recent_digest,
                    **_completion_fields(criteria),
                }
                return

            if content:
                yield {"type": "step_note", "step": step, "note_id": uuid.uuid4().hex, "text": content}
            if _history_checkpoint:
                _history_checkpoint(turn_context.messages)
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }
            reasoning_content = (response.get("message") or {}).get("reasoning_content")
            if isinstance(reasoning_content, str) and reasoning_content:
                assistant_message["reasoning_content"] = reasoning_content
            turn_context.messages.append(assistant_message)

            for call_index, call in enumerate(tool_calls):
                if _history_checkpoint:
                    _history_checkpoint(turn_context.messages)
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or {}
                parsed_args = _without_runtime_arguments(ToolRegistry._coerce_args(raw_args))
                runtime_context: dict[str, str] = {}
                if read_only and not delegate_tool_allowed(name, parsed_args):
                    denial = "Delegated inspection is read-only; this tool or operation is outside its scope."
                    turn_context.messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                                  "name": name, "content": denial})
                    yield {"type": "tool_call", "step": step, "tool": name,
                           "arguments": _public_arguments(parsed_args), "result": denial,
                           "ok": False, "error": "delegate_read_only", "dispatched": False,
                           "state_changed": False, "execution_status": "rejected"}
                    continue
                _read_requested_path = ""
                _read_recovered_from = ""
                if name == "read_file":
                    _read_resource_resolved = False
                    _read_requested_path = str(parsed_args.get("path") or "").strip()
                    _read_target = Path(_read_requested_path)
                    if not _read_target.is_absolute():
                        _read_target = root / _read_target
                    try:
                        _project_file_exists = _read_target.resolve().is_file()
                    except OSError:
                        _project_file_exists = False
                    _recovered_path = recover_read_path_from_glob(
                        root,
                        _read_requested_path,
                        _last_glob_matches,
                    )
                    if _recovered_path:
                        _read_recovered_from = _read_requested_path
                        parsed_args["path"] = _recovered_path
                    elif not _project_file_exists:
                        _requested_name = Path(_read_requested_path.replace("\\", "/")).name.casefold()
                        _resource_matches = [
                            ref for ref in (resource_refs or [])
                            if Path(str(ref.get("name") or "").replace("\\", "/")).name.casefold()
                            == _requested_name
                            and str(ref.get("resource_id") or "").strip()
                        ]
                        if len(_resource_matches) == 1:
                            _resource_ref = _resource_matches[0]
                            _read_resource_resolved = True
                            runtime_context["resource_id"] = str(
                                _resource_ref["resource_id"]
                            )
                            runtime_context["resource_name"] = str(
                                _resource_ref.get("name") or ""
                            )
                    if (
                        not _recovered_path
                        and not _read_resource_resolved
                        and _read_file_failures.get(_read_requested_path, 0) >= 2
                    ):
                        runtime_context["refuse_reason"] = (
                            "Сначала используй glob, ResourceRef или другой подтверждённый путь."
                        )
                if name in {"ask_user", "workflow_request"}:
                    workflow_runner = run_ask_user if name == "ask_user" else run_workflow_request
                    workflow_result = yield from workflow_runner(
                        parsed_args, step=step, cancel_event=cancel_event,
                        pause_for_workflow_request=pause_for_workflow_request,
                    )
                    if workflow_result.terminal is not None:
                        yield {**workflow_result.terminal,
                               **_completion_fields(criteria, terminated_incomplete=True)}
                        return
                    turn_context.messages.append({"role": "tool", "content": workflow_result.tool_text, "name": name})
                    tool_round_trips += 1
                    if name == "ask_user":
                        question = str(parsed_args.get("question") or "").strip()
                        call_log.append(f"ask_user({(question[:40] or '?')})")
                    elif workflow_result.response is not None:
                        kind = str(parsed_args.get("kind") or "").strip().lower()
                        call_log.append(f"workflow_request({kind})")
                    continue
                if name == "todo_update":
                    # P12.1: checklist mutations are bound to the current run.
                    # The model never chooses the run_id; executor policy/audit
                    # still applies below because todo_update is a normal tool.
                    parsed_args["run_id"] = rid
                    # Resumed runs may replace or reshape their checklist freely.
                if name == "delegate_task":
                    # P12.2: subagents are children of the current run. The
                    # model chooses role/task, not parent_run_id. The workflow
                    # permission is inherited so bypass stays bypass end-to-end.
                    parsed_args["run_id"] = rid
                    parsed_args["permission_mode"] = permission_mode
                if name == "resource_publish":
                    # Bind document-QA retry accounting to this run. The schema does
                    # not expose run_id, so the model cannot choose or reuse it.
                    parsed_args["run_id"] = rid
                if document_page_count_contract is not None:
                    is_published_document = (
                        name == "resource_publish"
                        and str(parsed_args.get("project_path") or "").strip().lower()
                        .endswith((".docx", ".pdf"))
                    )
                    if is_published_document:
                        # The user contract outranks a model-supplied guess/omission.
                        parsed_args["expected_page_count"] = document_page_count_contract
                # Phase is presentation-only. Evidence is recorded only after
                # successful executor return below, never from model intent.
                if RunEvidence.is_verification_tool(name, arguments=parsed_args):
                    _phase_event = _enter_verification_phase()
                    if _phase_event is not None:
                        yield _phase_event
                recovery = observations.before_dispatch(name, parsed_args, root=root, model_turn=step)
                if recovery is not None:
                    # Successful exit/output repetition is not a failed business
                    # result. Enforce a different observation before executing the
                    # same command again, even if the model ignores the advice.
                    yield {"type": "command_recovery", "step": step, **recovery,
                           "command_progress": command_progress.snapshot()}
                    turn_context.messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                     "name": name, "content": json.dumps(recovery, ensure_ascii=False)})
                    yield {"type": "tool_call", "step": step, "tool": name,
                           "arguments": _public_arguments(parsed_args), "result": recovery["text"],
                           "ok": False, "error": recovery["reason"], "dispatched": False,
                           "state_changed": False, "execution_status": "rejected",
                           "command_progress": command_progress.snapshot()}
                    if recovery["status"] == "blocked":
                        if _history_checkpoint:
                            _history_checkpoint(turn_context.messages, final_text=recovery["text"])
                        yield {"type": "final_response", "step": step, "text": recovery["text"],
                               "answer_state": "accepted", "answer_status": "degraded",
                               "task_outcome": task_outcome.snapshot()}
                        yield {"type": "done", "ok": False, "steps": step,
                               "stop_reason": "blocked", "resumable": True,
                               "answer_status": "degraded", "error": recovery["reason"],
                               "command_progress": command_progress.snapshot(),
                               **_completion_fields(criteria, terminated_incomplete=True)}
                        return
                    continue
                writes_memory = name == "memory" and str(parsed_args.get("action") or "").lower() == "add"
                memory_fact = parsed_args.get("text") or parsed_args.get("query")
                if writes_memory and not task_memory_write_allowed(run_persistence_policy(rid), memory_fact):
                    denial = "Долговременное сохранение запрещено политикой текущей задачи."
                    turn_context.messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                                  "name": name, "content": denial})
                    yield {"type": "tool_call", "step": step, "tool": name,
                           "arguments": _public_arguments(parsed_args), "result": denial,
                           "ok": False, "error": "task_persistence_denied", "dispatched": False,
                           "state_changed": False, "execution_status": "rejected"}
                    continue
                _request = ToolExecutionRequest(
                    run_id=rid,
                    agent_id=effective_agent_id,
                    project_scope_id=scope_id,
                    tool_name=name,
                    args=parsed_args,
                    runtime_context=runtime_context,
                    source="code_agent",
                    permission_mode=permission_mode,
                    workflow_approved=workflow_approval_matches(
                        pending_workflow_approval,
                        name,
                        parsed_args,
                    ),
                )
                if _request.workflow_approved:
                    pending_workflow_approval = {}
                execution = yield from run_tool(
                    _request, dispatch_fn=registry.dispatch_raw, executor=_kernel_exec,
                    step=step, cancel_event=cancel_event,
                    pause_for_workflow_request=pause_for_workflow_request,
                )
                if execution.terminal is not None:
                    yield {**execution.terminal,
                           **_completion_fields(criteria, terminated_incomplete=True)}
                    return
                _exec_result = execution.result
                tool_meta = _exec_result.output
                tool_meta, _skill_snapshot_changed, _skill_receipt = turn_context.activate_skill_result(
                    name=name, args=parsed_args, tool_meta=tool_meta, status=_exec_result.status,
                )
                _runtime_activation_snapshot: dict[str, Any] | None = None
                if str(tool_meta.get("status") or "").strip() in RUNTIME_WORKFLOW_STATUSES:
                    workflow_result = yield from run_runtime_workflow(
                        name, parsed_args, tool_meta, step=step, cancel_event=cancel_event,
                        pause_for_workflow_request=pause_for_workflow_request,
                    )
                    if workflow_result.terminal is not None:
                        yield {**workflow_result.terminal,
                               **_completion_fields(criteria, terminated_incomplete=True)}
                        return
                    turn_context.messages.append({"role": "tool", "content": workflow_result.tool_text, "name": name})
                    tool_round_trips += 1
                    call_log.append(f"{name}({str(tool_meta.get('status') or '').strip()})")
                    continue
                schema_update = activation.apply_tool_result(
                    name, parsed_args, tool_meta, status=_exec_result.status,
                    user_message=user_message,
                )
                if schema_update is not None:
                    registry, all_schemas = schema_update.registry, schema_update.schemas
                    _runtime_activation_snapshot = schema_update.snapshot
                if (
                    tool_meta.get("backgrounded") is True
                    and str(tool_meta.get("status") or "") == "running"
                    and tool_meta.get("pid") is not None
                ):
                    pending_redirected_jobs.add(int(tool_meta["pid"]))
                if name == "run_server" and parsed_args.get("pid") is not None:
                    _job_pid = int(parsed_args["pid"])
                    _job_status = str(tool_meta.get("status") or "")
                    if (
                        _job_pid in pending_redirected_jobs
                        and str(parsed_args.get("action") or "start").lower()
                        in {"logs", "stop"}
                        and _job_status in {"completed", "failed", "cancelled"}
                    ):
                        pending_redirected_jobs.discard(_job_pid)
                text_result = str(tool_meta.get("text", ""))
                _tool_ok = bool(tool_meta.get("ok", _exec_result.status == "ok"))
                script_hint = turn_context.script_skill_hint(name=name, args=parsed_args, ok=_tool_ok)
                if script_hint:
                    text_result += "\n\n" + script_hint
                    tool_meta["text"] = text_result
                if _tool_ok and name == "glob":
                    _last_glob_matches = tuple(
                        line.strip()
                        for line in text_result.splitlines()
                        if line.strip()
                        and not line.startswith("ERROR:")
                        and not line.startswith("No files match")
                    )
                if name == "read_file" and _read_requested_path:
                    if _tool_ok:
                        _read_file_failures.pop(_read_requested_path, None)
                    elif tool_meta.get("error") != "read_retry_exhausted":
                        _read_file_failures[_read_requested_path] = (
                            _read_file_failures.get(_read_requested_path, 0) + 1
                        )
                if _read_recovered_from:
                    _recovery_note = (
                        "[runtime: однозначно восстановил обрезанный путь из результата "
                        f"предыдущего glob: '{_read_recovered_from}' → "
                        f"'{parsed_args.get('path')}']\n"
                    )
                    text_result = _recovery_note + text_result
                    tool_meta["text"] = text_result
                    tool_meta["recovered_from"] = _read_recovered_from
                    tool_meta["recovered_path"] = str(parsed_args.get("path") or "")
                _failed_call = _exec_result.status != "ok" or tool_meta.get("ok") is False
                if _failed_call:
                    _failure_counts[name] = _failure_counts.get(name, 0) + 1
                    _failure_counts["__all__"] = _failure_counts.get("__all__", 0) + 1
                    _failure_error = str(
                        tool_meta.get("error") or text_result.split("\n", 1)[0]
                    )[:500]
                _state_changed = tool_state_changed(
                    name,
                    tool_meta,
                    exec_ok=(
                        _exec_result.status == "ok"
                        and bool(tool_meta.get("ok", True))
                    ),
                )
                event: dict[str, Any] = {
                    "type": "tool_call",
                    "step": step,
                    "tool": name,
                    "arguments": _public_arguments(parsed_args),
                    "result": _truncate(text_result),
                    "ok": bool(tool_meta.get("ok", _exec_result.status == "ok")),
                    # Server-owned mutation flag (ToolSpec.side_effect + real
                    # touched target on a successful execution). Read-only
                    # touched_path (read_file/ssh_read) stays False — delivery
                    # auto-continuation counts MUTATIONS, not reads.
                    "state_changed": _state_changed,
                }
                if _runtime_activation_snapshot is not None:
                    event["runtime_activation"] = _runtime_activation_snapshot
                if _skill_receipt is not None:
                    event["skill"] = _skill_receipt
                if (name in {"project_map", "glob", "grep", "run_bash", "write_file", "edit_file"}
                        or name.startswith(("itops_", "ssh_"))):
                    turn_context.skill_reminder_pending = True
                task_state_verification = ""
                if (
                    name == "run_bash"
                    and tool_meta.get("exit_code") is not None
                    and RunEvidence.is_verification_tool(
                        name,
                        arguments=parsed_args,
                        output=tool_meta,
                    )
                ):
                    from app.core.redaction import redact_text

                    command = redact_text(str(parsed_args.get("command") or ""))[:120]
                    task_state_verification = (
                        f"{command} → exit={tool_meta.get('exit_code')}"
                    )
                    event["task_state_verification"] = task_state_verification
                if not _tool_ok:
                    event["task_state_failure"] = f"{name}: error"
                for opt in (
                    "touched_path", "old_content", "new_content", "diff_action",
                    "exit_code", "verifier", "evidence", "download_url",
                    "download_name", "project_path", "size", "sha256", "document_qa",
                    "actual_url", "local_url", "actual_port", "port", "pid",
                    "server_started", "media", "action", "kind", "status",
                    "job_id", "log_path", "recovered",
                    "command", "cwd", "command_sha256", "output_sha256", "attempt_id",
                    "source_path", "source_sha256", "execution_id", "receipt_path",
                    "execution_status", "semantic_status",
                    "backgrounded", "redirected_from", "redirect_reason",
                    "remote_pid", "remote_pid_pending",
                    "remote_cleanup_supported", "remote_cleanup_status",
                    "remote_process_identity_captured", "remote_cleanup_ready",
                    "error", "recovered_from", "recovered_path",
                    "resolved_from_resource", "resource_id", "resource_name",
                    "sources", "engine_warnings", "query_sources", "resource", "chars", "preview_chars", "truncated_preview",
                    "selected_target", "execution_target",
                ):
                    if opt in tool_meta:
                        # Keep diff payloads truncated too to keep events small.
                        val = tool_meta[opt]
                        if isinstance(val, str) and opt in {"old_content", "new_content"} and len(val) > 40000:
                            event[opt] = val[:40000] + "\n[... truncated]"
                        else:
                            event[opt] = val
                observed_fields = observations.observe_result(
                    name=name, args=parsed_args, output=tool_meta, status=_exec_result.status,
                    text=text_result, state_changed=_state_changed, root=root,
                )
                event.update(observed_fields)
                recovery_context = observed_fields.get("recovery_context", "")
                if _skill_snapshot_changed:
                    yield {"type": "skills_changed", "step": step,
                           "active_skills": turn_context.skills.snapshots()}
                yield event
                tool_round_trips += 1
                _hint = _short_arg_hint(parsed_args)
                call_log.append(
                    f"{name}({_hint}) {'ok' if tool_meta.get('ok', True) else 'error'}"
                )
                if _failed_call:
                    _path = str(parsed_args.get("path") or parsed_args.get("command") or "").strip()
                    _err = str(tool_meta.get("error") or text_result.split("\n", 1)[0])[:180]
                    _last_failure = {"tool": name, "path": _path, "error": _err}
                elif _exec_result.status == "ok" and bool(tool_meta.get("ok", True)):
                    # A later successful operation makes an unrelated old failure a
                    # bad deterministic continuation hint. Keep historical failure
                    # evidence, but clear the ACTIVE failure context.
                    _last_failure = {}
                # Smart-truncate tool output before feeding it back to the
                # LLM. Without this, a single huge `run_bash` or `read_file`
                # could blow out `num_ctx` and start eating the system
                # prompt off the front of the context.
                result_limit = TOOL_RESULT_LLM_LIMIT
                _tool_content = _truncate_for_llm(text_result, limit=result_limit)
                if recovery_context:
                    _tool_content += "\n\n" + recovery_context
                # Grounding fact from this call — computed HERE (before the tool
                # message is appended) so the progress controller can judge whether
                # the call revealed anything NEW.
                _fact = _fact_from_tool(
                    name, _hint, text_result, ok=_tool_ok, verifier=bool(tool_meta.get("verifier")),
                )
                observations.complete_result(
                    name=name, args=parsed_args, output=tool_meta, status=_exec_result.status,
                    text=text_result, ok=_tool_ok, state_changed=_state_changed,
                    verification=task_state_verification,
                )
                _unchanged_read = turn_context.working_set.unchanged_read(
                    name=name, args=parsed_args, ok=_tool_ok, text=text_result,
                    messages=turn_context.messages,
                )
                _tool_message = {"role": "tool", "content": _unchanged_read or _tool_content, "name": name}
                turn_context.messages.append(_tool_message)
                if not _unchanged_read:
                    turn_context.working_set.record(
                        name=name, args=parsed_args, ok=_tool_ok, text=text_result, step=step,
                        message=_tool_message,
                    )
                if _fact:
                    established_facts.append(_fact)
                elif _failed_call:
                    # No grounding fact from a failure, but the deterministic stop
                    # summary must still name the concrete file/command + error.
                    established_facts.append(
                        f"НЕУДАЧА: {name}({_last_failure.get('path','')}) — "
                        f"{_last_failure.get('error','')}"
                    )
                _recent = _recent_tool_snippet(name, _hint, text_result)
                if _recent:
                    recent_tool_outputs.append(_recent)
            if _history_checkpoint:
                _history_checkpoint(turn_context.messages)

    finally:
        try:
            _unregister_run(rid)
        except Exception:
            pass


def stream_code_agent(
    *,
    user_message: str,
    memory_query: str | None = None,
    task_instructions: str = "",
    project_root: Path | str,
    working_dir: Path | str | None = None,
    model: str = "auto",
    agent_id: str = "code-agent",
    conversation_history: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    num_ctx: int | None = None,
    base_tools: tuple[str, ...] | list[str] | None = None,
    auto_remember: bool = True,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
    chat_stream_fn: Callable[..., Any] | None = None,
    resume: bool = False,
    profile_name: str = "Инженерный",
    permission_mode: str = "ask",
    thinking: bool = False,
    reasoning_effort: str | None = None,
    pause_for_workflow_request: bool = False,
    workflow_approval: dict[str, Any] | None = None,
    resource_refs: list[dict[str, Any]] | None = None,
    source_run_ids: list[str] | None = None,
    history_run_id: str | None = None,
    parent_run_id: str | None = None,
    read_only: bool = False,
    _server_history: list[dict[str, Any]] | None = None,
    _history_workflow_input_ids: tuple[str, ...] = (),
    _visible_request: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Journalled public stream around the existing model/tool runtime."""
    from app.application.code_agent.run_journal import RunJournal, discover_capabilities, related_sources, sanitize_event

    rid = run_id or uuid.uuid4().hex
    initial_tools = tuple(base_tools) if base_tools is not None else _CODE_AGENT_BASE_TOOLS
    journal = RunJournal.load(rid) if resume else RunJournal(rid, parent_run_id=parent_run_id)
    if _visible_request is None and not resume:
        _visible_request = {"history": list(conversation_history or []), "current": {
            "role": "user", "content": memory_query if isinstance(memory_query, str) else user_message,
        }}
    if not resume and history_run_id:
        _server_history = RunJournal.history_for_next_message(
            history_run_id, session_id=session_id, project_root=project_root,
            conversation_history=list((_visible_request or {}).get("history", conversation_history or [])),
            prepared_history=conversation_history,
        )
    read_only = bool(read_only or resume and (journal.state.get("request") or {}).get("read_only"))
    persistence_policy = (
        persistence_policy_from_state(journal.state) if resume else task_persistence_policy(
            memory_query if isinstance(memory_query, str) else user_message, auto_remember=auto_remember,
            trusted_user_text=isinstance(memory_query, str),
        )
    )
    selected_reasoning_effort = _normalize_reasoning_effort(
        reasoning_effort,
        thinking=thinking,
    )
    request = {
        "user_message": user_message,
        "memory_query": memory_query,
        "task_instructions": task_instructions,
        "project_root": str(project_root),
        "working_dir": str(working_dir) if working_dir is not None else None,
        "model": model,
        "agent_id": agent_id,
        "conversation_history": conversation_history or [],
        "session_id": session_id,
        "num_ctx": int(num_ctx) if num_ctx else None,
        "base_tools": list(initial_tools),
        "auto_remember": bool(auto_remember),
        "persistence_policy": persistence_policy,
        "profile_name": profile_name,
        "permission_mode": permission_mode,
        "thinking": selected_reasoning_effort != "none",
        "reasoning_effort": selected_reasoning_effort,
        "pause_for_workflow_request": bool(pause_for_workflow_request),
        "resource_refs": list(resource_refs or []),
        "source_run_ids": list(source_run_ids or [])[-8:],
        "history_run_id": history_run_id,
        "parent_run_id": parent_run_id,
        "delegation_depth": 1 if parent_run_id else 0,
        "read_only": read_only,
    }
    terminal = False
    core_stream = None
    answer_media = merge_answer_media(
        [],
        journal.state.get("answer_media") if resume else [],
    )
    try:
        if resume:
            journal.resume()
            resume_event = {
                "type": "run_resumed",
                "run_id": rid,
                "from_step": int(journal.state.get("last_successful_step") or 0),
            }
            journal.append_event(resume_event)
            yield resume_event
        else:
            capabilities = discover_capabilities(model=model, tools=list(initial_tools))
            journal.start(request, capabilities)
            for missing in capabilities.get("missing", []):
                journal.append_event({
                    "type": "missing_capability",
                    "capability": missing,
                })
        journal.append_event({"type": "persistence_policy_changed", "persistence_policy": persistence_policy})

        def audit_sink(payload: dict[str, Any]) -> None:
            journal.append_event({"type": "compaction_audit", **payload})

        def history_checkpoint(messages: list[dict[str, Any]], **kwargs: Any) -> None:
            try:
                journal.checkpoint_model_history(messages, represented_workflow_ids=_history_workflow_input_ids,
                                                 visible_request=_visible_request, **kwargs)
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("model history checkpoint unavailable for %s: %s", rid, exc)

        core_stream = _stream_code_agent_core(
            user_message=user_message,
            memory_query=memory_query,
            task_instructions=task_instructions,
            project_root=project_root,
            working_dir=working_dir,
            model=model,
            agent_id=agent_id,
            conversation_history=conversation_history,
            run_id=rid,
            num_ctx=num_ctx,
            base_tools=initial_tools,
            auto_remember=auto_remember,
            chat_fn=chat_fn,
            chat_stream_fn=chat_stream_fn,
            compaction_audit_sink=audit_sink,
            profile_name=profile_name,
            permission_mode=permission_mode,
            thinking=thinking,
            reasoning_effort=selected_reasoning_effort,
            resume=resume,
            pause_for_workflow_request=pause_for_workflow_request,
            workflow_approval=workflow_approval,
            resource_refs=resource_refs,
            initial_sources=(
                list(journal.state.get("web_sources") or []) if resume
                else related_sources(session_id, list(source_run_ids or []), list(conversation_history or []))
            ),
            read_only=read_only,
            _server_history=_server_history,
            _history_checkpoint=history_checkpoint,
        )
        for raw_event in core_stream:
            # Display/persistence only: keep canonical tool output and approval
            # digests inside the executor unchanged.
            event = sanitize_event(raw_event)
            event.setdefault("run_id", rid)
            direct_input = None
            if event.get("type") == "user_input_applied":
                direct_input = event.get("text")
            elif event.get("type") == "tool_call" and event.get("ok") is True:
                workflow_input = event.get("workflow_input")
                if isinstance(workflow_input, dict):
                    direct_input = workflow_input.get("answer")
            if isinstance(direct_input, str):
                next_policy = task_persistence_policy(direct_input, saved=persistence_policy,
                                                      auto_remember=auto_remember)
                if next_policy != persistence_policy:
                    persistence_policy = next_policy
                    journal.append_event({"type": "persistence_policy_changed",
                                          "persistence_policy": persistence_policy})
            if resume and event.get("type") == "run_started":
                continue
            if event.get("type") == "done":
                event.setdefault("resumable", bool(
                    event.get("partial")
                    or event.get("stop_reason")
                    in {"error", "context_limit", "workflow_request"}
                ))
                terminal = True
            if event.get("type") == "tool_call" and event.get("media"):
                answer_media = merge_answer_media(answer_media, event.get("media"))
            if event.get("type") == "final_response" and answer_media:
                event["media"] = list(answer_media)
            if event.get("type") == "tool_started":
                journal.append_event({
                    "type": "tool_decision",
                    "step": event.get("step"),
                    "tool": event.get("tool"),
                    "reason": "selected by routed code model",
                })
                if event.get("tool") == "run_bash":
                    journal.append_event({
                        "type": "command_started",
                        "step": event.get("step"),
                        "command": (event.get("arguments") or {}).get("command"),
                    })
            if event.get("type") in {"tool_started", "tool_call"}:
                journal.append_command(event)
            journal.append_event(event)
            if event.get("type") == "tool_call":
                result_text = str(event.get("result") or "")
                tool_ok = bool(event.get("ok", not result_text.lower().startswith("error")))
                journal.append_event({
                    "type": "tool_completed" if tool_ok else "tool_failed",
                    "step": event.get("step"),
                    "tool": event.get("tool"),
                    "ok": tool_ok,
                })
                if event.get("tool") == "run_bash":
                    journal.append_event({
                        "type": "command_output_tail",
                        "step": event.get("step"),
                        "output": result_text[-4000:],
                        "ok": tool_ok,
                    })
                if event.get("touched_path"):
                    journal.append_event({
                        "type": "file_changed",
                        "step": event.get("step"),
                        "path": event.get("touched_path"),
                        "action": event.get("diff_action"),
                    })
            elif event.get("type") == "usage":
                journal.append_event({
                    "type": "model_call",
                    "step": event.get("step"),
                    "model": model,
                    "prompt_tokens": event.get("prompt_tokens"),
                    "completion_tokens": event.get("completion_tokens"),
                    "tokens_per_second": event.get("tokens_per_second"),
                })
            elif event.get("type") == "done":
                journal.append_event({
                    "type": "run_completed" if event.get("ok") else "run_failed",
                    "steps": event.get("steps"),
                    "stop_reason": event.get("stop_reason"),
                    "resumable": event.get("resumable"),
                })
            yield event
    except Exception as exc:
        logger.exception("code-agent run journal failed for %s", rid)
        event = {
            "type": "done",
            "run_id": rid,
            "ok": False,
            "steps": int(journal.state.get("last_successful_step") or 0),
            "stop_reason": "error",
            "error": str(exc),
            "resumable": True,
        }
        try:
            journal.append_event(event)
        except Exception:
            logger.exception("failed to persist terminal event for %s", rid)
        terminal = True
        yield event
    finally:
        try:
            # This adapter owns the iterator even when another reference keeps
            # it alive. Close before finishing the journal so upstream transport
            # and live-run accounting do not depend on garbage collection.
            if core_stream is not None:
                core_stream.close()
        finally:
            journal.finish(interrupted=not terminal)


def run_code_agent(
    *,
    user_message: str,
    memory_query: str | None = None,
    task_instructions: str = "",
    project_root: Path | str,
    working_dir: Path | str | None = None,
    model: str = "auto",
    agent_id: str = "code-agent",
    conversation_history: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    num_ctx: int | None = None,
    base_tools: tuple[str, ...] | list[str] | None = None,
    auto_remember: bool = True,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
    chat_stream_fn: Callable[..., Any] | None = None,
    profile_name: str = "Инженерный",
    permission_mode: str = "ask",
    thinking: bool = False,
    reasoning_effort: str | None = None,
    pause_for_workflow_request: bool = False,
    resume: bool = False,
    workflow_approval: dict[str, Any] | None = None,
    resource_refs: list[dict[str, Any]] | None = None,
    parent_run_id: str | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    """Synchronous single-shot wrapper around stream_code_agent. Drains
    the generator and aggregates the result into the legacy dict shape.
    Workflow input waits until a UI decision or Stop.
    """
    tool_calls_log: list[dict[str, Any]] = []
    response_text = ""
    response_media: list[dict[str, str]] = []
    response_citations: list[dict[str, Any]] = []
    response_sources: list[dict[str, Any]] = []
    source_status = "none"
    answer_status = "degraded"
    ok = False
    stop_reason = "error"
    error: str | None = None
    partial = False
    steps = 0
    # FIX-2: task-state must survive the sync path so API/automation can't see
    # only runtime `ok` without the task result.
    completion_status = "none"
    criteria: list[dict[str, Any]] = []
    criteria_confirmed = False
    task_spec: dict[str, Any] | None = None
    request_status = ""
    request: dict[str, Any] | None = None
    response_id = ""

    for event in stream_code_agent(
        user_message=user_message,
        memory_query=memory_query,
        task_instructions=task_instructions,
        project_root=project_root,
        working_dir=working_dir,
        model=model,
        agent_id=agent_id,
        conversation_history=conversation_history,
        run_id=run_id,
        num_ctx=num_ctx,
        base_tools=base_tools,
        auto_remember=auto_remember,
        chat_fn=chat_fn,
        chat_stream_fn=chat_stream_fn,
        profile_name=profile_name,
        permission_mode=permission_mode,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        pause_for_workflow_request=pause_for_workflow_request,
        resume=resume,
        workflow_approval=workflow_approval,
        resource_refs=resource_refs,
        parent_run_id=parent_run_id,
        read_only=read_only,
    ):
        et = event.get("type")
        if et == "step_started":
            response_text = ""
        elif et == "delta":
            response_text += str(event.get("text") or "")
        elif et == "tool_call":
            tool_calls_log.append({
                "step": event["step"],
                "tool": event["tool"],
                "arguments": event["arguments"],
                "result": event["result"],
                **{k: event[k] for k in (
                    "ok", "exit_code", "verifier", "evidence",
                    "touched_path", "old_content", "new_content", "diff_action",
                    "download_url", "download_name", "project_path", "size", "sha256",
                    "actual_url", "local_url", "actual_port", "port", "pid",
                    "server_started", "media", "sources", "engine_warnings", "query_sources", "skill",
                ) if k in event},
            })
        elif et == "final_response":
            response_text = event.get("text", "")
            answer_status = str(event.get("answer_status") or "complete")
            response_media = list(event.get("media") or [])
            response_citations = list(event.get("citations") or [])
            response_sources = list(event.get("sources") or [])
            source_status = str(event.get("source_status") or "none")
        elif et == "workflow_request":
            request_status = str(event.get("status") or "")
            raw_request = event.get("request")
            request = dict(raw_request) if isinstance(raw_request, dict) else None
            response_id = str(event.get("response_id") or "")
        elif et == "done":
            ok = bool(event.get("ok"))
            partial = bool(event.get("partial"))
            answer_status = str(event.get("answer_status") or answer_status)
            stop_reason = str(event.get("stop_reason", "error"))
            error = event.get("error")
            steps = int(event.get("steps", 0))
            completion_status = str(event.get("completion_status") or "none")
            criteria = list(event.get("criteria") or [])
            criteria_confirmed = bool(event.get("criteria_confirmed"))
            task_spec = event.get("task_spec")
            request_status = str(event.get("status") or request_status)
            raw_request = event.get("request")
            if isinstance(raw_request, dict):
                request = dict(raw_request)
            response_id = str(event.get("response_id") or response_id)

    return {
        "ok": ok,
        "response": response_text,
        "media": response_media,
        "citations": response_citations,
        "sources": response_sources,
        "source_status": source_status,
        "answer_status": answer_status,
        "steps": steps,
        "tool_calls": tool_calls_log,
        "stop_reason": stop_reason,
        "error": error,
        "partial": partial,
        # Task result — SEPARATE from runtime `ok`. Consumers must gate "solved"
        # on completion_status == "confirmed", never on ok alone.
        "completion_status": completion_status,
        "criteria": criteria,
        "criteria_confirmed": criteria_confirmed,
        "task_spec": task_spec,
        "status": request_status,
        "request": request,
        "response_id": response_id,
    }
