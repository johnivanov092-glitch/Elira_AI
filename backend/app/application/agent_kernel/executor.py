"""Unified ToolExecutor — single execution path for all agent sources.

Flow per call:
  1. Resolve ToolSpec. Fail-closed gates (each emits tool.invalid_spec, blocks
     before dispatch): no spec (unknown_toolspec), policy_classified=0
     (unclassified_tool), invalid permission tier, unknown scope in the spec.
  1e. Forbidden tier — a classified permission == "forbidden" is blocked immediately.
  2. Policy preflight via sandbox (rate-limit + context-budget + per-call allowed_tools).
  3. Approval gate — permission == "require_approval" requires a valid human
     approval (ApprovalStore in agent_monitor.db); otherwise the call returns
     "waiting_approval" until one is granted.
  4. Dispatch via caller-supplied dispatch_fn.
  5. Truncate text output to max_output_chars.
  6. Emit tool.executed / sandbox.policy.blocked / tool.approval_pending to the event bus.
  7. Return ToolExecutionResult (ok | error | blocked | forbidden | waiting_approval).

Design: dispatch_fn is injected by callers so that:
  - Chat/workflow passes a function backed by tool_registry database handlers.
  - Code-agent passes ToolRegistry.dispatch_raw (Builtin + SSH + MCP providers).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable


DispatchFn = Callable[[str, dict[str, Any]], dict[str, Any]]

_TRUNCATION_SUFFIX = "\n[output truncated]"
logger = logging.getLogger(__name__)


def _strict_arguments_valid(schema: Any, args: Any) -> bool:
    """Validate the small strict-object JSON-schema subset used by ToolSpecs.

    Non-strict schemas keep their existing handler-owned validation.  A strict
    schema (``additionalProperties: false``) is fail-closed here so unexpected
    arguments cannot be persisted in an approval before the handler sees them.
    """
    if not isinstance(schema, dict) or schema.get("additionalProperties") is not False:
        return True
    if schema.get("type") != "object" or not isinstance(args, dict):
        return False
    properties = schema.get("properties")
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        return False
    if any(not isinstance(name, str) for name in required):
        return False
    if any(name not in properties for name in args) or any(name not in args for name in required):
        return False

    type_checks = {
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "array": lambda value: isinstance(value, list),
        "object": lambda value: isinstance(value, dict),
        "null": lambda value: value is None,
    }
    for name, value in args.items():
        rule = properties.get(name)
        if not isinstance(rule, dict):
            return False
        expected = rule.get("type")
        if expected is not None:
            expected_types = expected if isinstance(expected, list) else [expected]
            if not expected_types or any(not isinstance(item, str) for item in expected_types):
                return False
            if not any(
                (check := type_checks.get(item)) is not None and check(value)
                for item in expected_types
            ):
                return False
        allowed = rule.get("enum")
        if allowed is not None and (not isinstance(allowed, list) or value not in allowed):
            return False
        pattern = rule.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str) or not isinstance(value, str):
                return False
            try:
                if re.search(pattern, value) is None:
                    return False
            except re.error:
                return False
    return True


@dataclass
class ToolExecutionRequest:
    run_id: str
    agent_id: str
    project_scope_id: str
    tool_name: str
    args: dict[str, Any]
    source: str
    workflow_id: str = ""
    step_id: str = ""


@dataclass
class ToolExecutionResult:
    status: str  # "ok" | "error" | "blocked" | "forbidden" | "waiting_approval"
    output: dict[str, Any]
    error: str | None = None


def _itops_profile_enabled(profile_id: str) -> bool:
    """True iff *profile_id* is a saved SSH profile on an ENABLED (verified) asset.

    Fail-closed: any error (store unavailable, unknown profile, draft asset) → False,
    so the scoped health-check gate blocks rather than falls through.
    """
    try:
        from app.infrastructure.it_ops import store as _st
        prof = _st.get_connection_profile(profile_id)
        if not prof or prof.get("transport") != "ssh":
            return False
        asset = _st.get_asset(str(prof.get("asset_id") or ""))
        return bool(asset) and asset.get("lifecycle_state") == "enabled"
    except Exception:  # noqa: BLE001 — fail-closed
        return False


def execute_tool(
    request: ToolExecutionRequest,
    dispatch_fn: DispatchFn,
) -> ToolExecutionResult:
    """Execute one tool call through the unified policy and audit layer."""
    from app.application.tool_registry.runtime import get_tool
    from app.application.agent_registry.sandbox import SandboxPolicyError, preflight_or_raise

    from app.application.tool_registry.store import (
        VALID_PERMISSIONS as _VALID_PERMS,
        VALID_SCOPES as _VALID_SCOPES,
    )

    tool_name = request.tool_name

    # 1. Resolve ToolSpec. The kernel is fail-closed: a tool with no spec, an
    # unclassified spec, an invalid permission tier, or an unknown scope in its
    # persisted spec must NEVER reach dispatch. Each such case emits
    # tool.invalid_spec so the integrity problem is auditable.
    spec = get_tool(tool_name)

    # 1a. No spec at all → unknown to the policy layer. Block before dispatch.
    if spec is None:
        _emit_invalid_spec(request, "unknown_toolspec")
        return ToolExecutionResult(
            status="blocked",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' has no registered ToolSpec — blocked (fail-closed).",
                "error": "unknown_toolspec",
            },
            error="unknown_toolspec",
        )

    max_chars: int = int(spec.get("max_output_chars") or 50000)

    # 1b. Unclassified spec → never runs until an admin classifies it (Tool API
    # PATCH sets policy_classified=true). Covers freshly-discovered plugin/MCP
    # tools and any row migrated in before classification existed.
    if not spec.get("policy_classified"):
        _emit_invalid_spec(request, "unclassified_tool")
        return ToolExecutionResult(
            status="blocked",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' is not policy-classified — blocked (fail-closed).",
                "error": "unclassified_tool",
            },
            error="unclassified_tool",
        )

    # 1b'. Disabled spec → not executable through the unified path either. Defense
    # in depth so a classified-but-disabled tool (e.g. an admin-classified plugin
    # left disabled, or a stale/disabled MCP tool) cannot run via the kernel.
    if not spec.get("enabled", True):
        _emit_blocked(request, f"tool '{tool_name}' is disabled")
        return ToolExecutionResult(
            status="blocked",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' is disabled and cannot be executed.",
                "error": "tool_disabled",
            },
            error="tool_disabled",
        )

    # 1c. Invalid permission tier → must never be treated as "auto".
    if spec.get("permission") not in _VALID_PERMS:
        _emit_invalid_spec(request, f"invalid_permission:{spec.get('permission')!r}")
        return ToolExecutionResult(
            status="blocked",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' has an invalid permission tier and cannot be executed.",
                "error": "invalid_permission",
            },
            error="invalid_permission",
        )

    # 1d. Unknown scope in the persisted spec → fail-closed (a corrupted or
    # forward-dated spec must not slip an unrecognized capability past the gate).
    _bad_scopes = [s for s in (spec.get("scopes") or []) if s not in _VALID_SCOPES]
    if _bad_scopes:
        _emit_invalid_spec(request, f"unknown_scope:{_bad_scopes}")
        return ToolExecutionResult(
            status="blocked",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' declares unknown scope(s) {_bad_scopes} — blocked (fail-closed).",
                "error": "unknown_scope",
            },
            error="unknown_scope",
        )

    # 1e. Forbidden tier — a classified, deliberate admin block. No approval possible.
    if spec.get("permission") == "forbidden":
        _emit_blocked(request, f"tool '{tool_name}' is forbidden")
        return ToolExecutionResult(
            status="forbidden",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' is permanently forbidden and cannot be executed.",
                "error": f"forbidden:{tool_name}",
            },
            error=f"forbidden:{tool_name}",
        )

    # 1f. Deferred tool activation (P10.1). When a run has opted into deferred
    # tool search, only tools activated for that run may execute — a tool the
    # model named without activating it (e.g. a guessed name) is blocked here,
    # before dispatch. Runs that never opted in have no entry and are unaffected
    # (inert by default). Activation grants visibility only: the policy / scope /
    # approval gates below still apply.
    from app.application.agent_kernel.deferred_tools import is_deferred_run, is_tool_active

    if is_deferred_run(request.run_id) and not is_tool_active(request.run_id, tool_name):
        _emit_blocked(request, f"tool '{tool_name}' not activated for run")
        return ToolExecutionResult(
            status="blocked",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' is not activated for this run — activate it via tool_search first.",
                "error": "tool_not_activated",
            },
            error="tool_not_activated",
        )

    # 1g. Operation-scope capability allowlist (Scoped Read-Only SSH). A run with a
    # bound operation scope is a LOCKED-DOWN read-only diagnostic run that permits
    # exactly ONE adapter tool (chosen server-side): only tool_search (discovery) and
    # that one bound tool may execute — every other tool (including OTHER itops
    # adapters, ssh_*, run_bash, filesystem, browser, change tools) is blocked here at
    # the single dispatch path. The allowlist is the EXACT bound tool NAME (not "any
    # itops tool"), so a future itops tool is never auto-runnable from an old scope.
    # An itops READ adapter can NEVER run without a bound scope naming it, over the
    # exact target. The one explicit exception is itops_change_apply: it is not a
    # diagnostic adapter; it is a require_approval side-effect tool whose target_id is
    # revalidated by the isolated change executor. It remains blocked inside any
    # locked diagnostic run by clause (a).
    _is_itops_tool = spec.get("source") == "itops"
    _requires_operation_scope = _is_itops_tool and tool_name != "itops_change_apply"
    try:
        from app.application.agent_kernel import operation_scope as _opscope
        # STICKY allowlist (locked_tool): once bound, a run stays locked until it ends
        # — a TTL expiry must NEVER re-open tools it was barred from. The one-shot
        # operation needs a LIVE scope (get_active_scope), which TTL does gate.
        _locked_tool = _opscope.locked_tool(request.run_id)
        _scope = _opscope.get_active_scope(request.run_id)
    except Exception as exc:  # noqa: BLE001 — a broken scope layer must BLOCK, not fall open
        # Fail CLOSED for a diagnostic run (server-minted run_id prefix — literal, so
        # this holds even if the import above is what failed) and for any itops adapter
        # tool: if we cannot verify the lockdown we block. Normal runs are unaffected.
        _diag_run = str(request.run_id or "").startswith("itops-diag-")
        if _diag_run or _requires_operation_scope:
            _emit_blocked(request, f"operation scope unavailable: {exc}")
            return ToolExecutionResult(
                status="blocked",
                output={"ok": False, "text": "Operation scope unavailable — blocked (fail-closed).",
                        "error": "scope_unavailable"},
                error="scope_unavailable",
            )
        _locked_tool, _scope = None, None

    # (a) In a locked-down run, allow ONLY tool_search + the EXACT bound adapter tool.
    if _locked_tool is not None and tool_name not in ("tool_search", _locked_tool):
        _emit_blocked(request, f"tool '{tool_name}' blocked: scoped read-only diagnostic run")
        return ToolExecutionResult(
            status="blocked",
            output={"ok": False,
                    "text": (f"Tool '{tool_name}' is not allowed in this scoped read-only "
                             f"diagnostic run — only tool_search and '{_locked_tool}' may run here."),
                    "error": "scope_restricted"},
            error="scope_restricted",
        )

    # (b) An itops adapter tool requires a LIVE scope bound to it, over the exact
    # profile, on an ENABLED asset, with its one operation slot unused. (If a scope is
    # bound to a DIFFERENT adapter, clause (a) already blocked this call.)
    if _requires_operation_scope:
        if _scope is None:
            _emit_blocked(request, f"{tool_name} requires an operation scope")
            return ToolExecutionResult(
                status="blocked",
                output={"ok": False, "text": "This diagnostic tool requires a bound read-only "
                        "operation scope (start a diagnostic run) — blocked (fail-closed).",
                        "error": "no_operation_scope"},
                error="no_operation_scope",
            )
        if _scope.mode != "read_only":
            _emit_blocked(request, "operation scope is not read_only")
            return ToolExecutionResult(
                status="blocked",
                output={"ok": False, "text": "Scope is not read-only — blocked (fail-closed).",
                        "error": "scope_mismatch"},
                error="scope_mismatch",
            )
        # Target validation is TYPED by scope.target_kind (one typed variant in the
        # single operation_scope — not a second executor, not a loose params dict). The
        # allowlist (clause a) and reserve below are shared across variants.
        if _scope.target_kind == "ssh_profile":
            _pid = str(request.args.get("profile_id") or "").strip()
            if not _pid or _pid != _scope.profile_id:
                _emit_blocked(request, "operation scope profile mismatch")
                return ToolExecutionResult(
                    status="blocked",
                    output={"ok": False, "text": "Requested profile_id is not the one bound to this "
                            "run's read-only scope — blocked (fail-closed).", "error": "scope_mismatch"},
                    error="scope_mismatch",
                )
            if not _itops_profile_enabled(_pid):
                _emit_blocked(request, "operation scope target is not an enabled asset")
                return ToolExecutionResult(
                    status="blocked",
                    output={"ok": False, "text": "The bound profile is not a verified/enabled asset "
                            "(draft or missing) — blocked (fail-closed).", "error": "profile_not_enabled"},
                    error="profile_not_enabled",
                )
            _authoritative_args: dict[str, Any] = {"profile_id": _pid}
        elif _scope.target_kind == "network":
            # A network adapter takes NO args: the CIDR + server-owned port profile come
            # ONLY from the scope. ANY model-supplied argument is blocked; the handler
            # reads the bound target from the run's scope (get_active_scope), not args.
            if request.args:
                _emit_blocked(request, "network adapter takes no args")
                return ToolExecutionResult(
                    status="blocked",
                    output={"ok": False, "text": "This adapter takes no arguments; the target comes "
                            "only from the bound scope — blocked (fail-closed).", "error": "scope_args_forbidden"},
                    error="scope_args_forbidden",
                )
            _authoritative_args = {}
        elif _scope.target_kind == "systemd_service":
            # A systemd inspect takes NO args: the profile_id (principal) + the selected
            # unit come ONLY from the scope. Forbid any model-supplied arg, AND (defense
            # in depth) require the bound profile to still be an ENABLED asset.
            if request.args:
                _emit_blocked(request, "systemd adapter takes no args")
                return ToolExecutionResult(
                    status="blocked",
                    output={"ok": False, "text": "This adapter takes no arguments; the target comes "
                            "only from the bound scope — blocked (fail-closed).", "error": "scope_args_forbidden"},
                    error="scope_args_forbidden",
                )
            _pid = str(_scope.profile_id or "").strip()
            if not _pid or not _itops_profile_enabled(_pid):
                _emit_blocked(request, "operation scope target is not an enabled asset")
                return ToolExecutionResult(
                    status="blocked",
                    output={"ok": False, "text": "The bound profile is not a verified/enabled asset "
                            "(draft or missing) — blocked (fail-closed).", "error": "profile_not_enabled"},
                    error="profile_not_enabled",
                )
            _authoritative_args = {}
        elif _scope.target_kind == "config_file":
            # A config inspect takes NO args. The enabled profile and named config target
            # come only from the server-bound scope; paths/keys/values are never model input.
            if request.args:
                _emit_blocked(request, "config adapter takes no args")
                return ToolExecutionResult(
                    status="blocked",
                    output={"ok": False, "text": "This adapter takes no arguments; the target comes "
                            "only from the bound scope — blocked (fail-closed).",
                            "error": "scope_args_forbidden"},
                    error="scope_args_forbidden",
                )
            _pid = str(_scope.profile_id or "").strip()
            if _scope.config is None or not _pid or not _itops_profile_enabled(_pid):
                _emit_blocked(request, "operation scope target is not an enabled config asset")
                return ToolExecutionResult(
                    status="blocked",
                    output={"ok": False, "text": "The bound profile/config target is unavailable "
                            "or not verified/enabled — blocked (fail-closed).",
                            "error": "profile_not_enabled"},
                    error="profile_not_enabled",
                )
            _authoritative_args = {}
        elif _scope.target_kind == "database":
            # A database inspect takes NO args. The database id, engine, path, allowed
            # schemas and fixed query profile come only from the server-bound scope.
            if request.args:
                _emit_blocked(request, "database adapter takes no args")
                return ToolExecutionResult(
                    status="blocked",
                    output={"ok": False, "text": "This adapter takes no arguments; the database "
                            "target comes only from the bound scope — blocked (fail-closed).",
                            "error": "scope_args_forbidden"},
                    error="scope_args_forbidden",
                )
            if _scope.database is None:
                _emit_blocked(request, "operation scope has no database target")
                return ToolExecutionResult(
                    status="blocked",
                    output={"ok": False, "text": "The bound database target is unavailable — "
                            "blocked (fail-closed).", "error": "scope_mismatch"},
                    error="scope_mismatch",
                )
            _authoritative_args = {}
        else:
            _emit_blocked(request, f"unknown scope target_kind: {_scope.target_kind}")
            return ToolExecutionResult(
                status="blocked",
                output={"ok": False, "text": "Scope has an unknown target kind — blocked (fail-closed).",
                        "error": "scope_unknown_target"},
                error="scope_unknown_target",
            )
        # One read-only operation per run: atomic reserve BEFORE dispatch. A second
        # call (or a retry after a failed dispatch) is refused.
        if not _opscope.reserve_operation(request.run_id):
            _emit_blocked(request, "operation already used for this run")
            return ToolExecutionResult(
                status="blocked",
                output={"ok": False, "text": "This diagnostic run has already performed its one "
                        "read-only operation — blocked.", "error": "operation_already_used"},
                error="operation_already_used",
            )
        # Hand the handler ONLY the authoritative, server-owned args (never model args).
        request.args = _authoritative_args

    # 2. Policy preflight — rate-limit, context-budget, and per-call allowed_tools.
    # selected_tools=[tool_name] enforces the agent's allowed_tools list at tool-call
    # granularity. An empty allowed_tools grant means unrestricted, so default agents
    # are unaffected; an explicitly tool-restricted agent is blocked per call here.
    try:
        preflight_or_raise(
            agent_id=request.agent_id,
            num_ctx=0,
            selected_tools=[tool_name],
            run_id=request.run_id,
            workflow_id=request.workflow_id,
            step_id=request.step_id,
            route=request.source,
        )
    except SandboxPolicyError as exc:
        _emit_blocked(request, str(exc))
        return ToolExecutionResult(
            status="blocked",
            output={"ok": False, "text": f"ERROR: sandbox blocked '{tool_name}': {exc}", "error": str(exc)},
            error=str(exc),
        )

    # 2b. Scope enforcement (P9.2A2). A tool may run only if its declared scopes
    # are within the agent's granted scopes. An empty grant means "unrestricted"
    # (mirrors allowed_tools), so existing flows are unaffected until an agent is
    # explicitly scope-restricted.
    _tool_scopes = list((spec or {}).get("scopes") or [])
    if _tool_scopes:
        try:
            from app.application.monitoring import runtime as _mon
            _granted = {str(s) for s in ((_mon.get_agent_limit(request.agent_id) or {}).get("allowed_scopes") or [])}
        except Exception as exc:
            logger.debug("failed to read agent scopes", exc_info=exc)
            _granted = set()
        if _granted:  # empty grant = unrestricted
            _missing = [s for s in _tool_scopes if s not in _granted]
            if _missing:
                _emit_blocked(request, f"scope_block:{_missing}")
                return ToolExecutionResult(
                    status="blocked",
                    output={
                        "ok": False,
                        "text": f"Tool '{tool_name}' requires scopes {_missing} not granted to agent '{request.agent_id}'.",
                        "error": "scope_block:" + ",".join(_missing),
                    },
                    error="scope_block",
                )

    # 2c. Strict ToolSpec arguments are validated before the approval store.
    # Handler-level checks remain defense in depth, but they are too late to
    # prevent an injected URL/token/path from appearing in an approval card.
    if not _strict_arguments_valid(spec.get("parameters_schema"), request.args):
        _emit_blocked(request, "invalid_tool_arguments")
        return ToolExecutionResult(
            status="blocked",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' received invalid arguments and was blocked.",
                "error": "invalid_tool_arguments",
            },
            error="invalid_tool_arguments",
        )

    # 3. Approval gate (implemented in Шаг 4/P1) — require_approval tools
    # need a valid human approval before dispatch.
    #
    # Exception: run_bash with a read-only allowlisted command auto-executes
    # without approval (P4 Шаг 11). This covers status/inspection commands
    # like `git status`, `ls`, `pytest --collect-only`, etc.
    _needs_approval = bool(spec and spec.get("permission") == "require_approval")
    # A remote Telegram IT call cannot change the host here: dispatch only asks the
    # isolated executor to create an immutable plan, whose dedicated bot performs the
    # actual approve/reject gate. Requiring the generic approval first would create two
    # approvals and leave remote work unable to reach the dedicated change channel.
    if request.agent_id == "telegram" and tool_name == "itops_change_apply":
        _needs_approval = False
    if _needs_approval and tool_name == "run_bash":
        _cmd = str(request.args.get("command", "")).strip()
        if _cmd:
            try:
                from app.application.code_agent.tools import is_shell_safe
                if is_shell_safe(_cmd):
                    _needs_approval = False
            except Exception as exc:
                # Conservative: keep approval requirement on import error.
                logger.debug("shell safety classification failed", exc_info=exc)

    if _needs_approval:
        # A stable run_id is required so the approval can be matched on retry.
        # Block empty AND whitespace-only run_id uniformly.
        if not str(request.run_id or "").strip():
            _emit_blocked(request, "approval_requires_run_id")
            return ToolExecutionResult(
                status="blocked",
                output={
                    "ok": False,
                    "text": (
                        f"Tool '{tool_name}' requires approval but the request carries no run_id. "
                        "Retry with a stable run_id so the approval can be matched."
                    ),
                    "error": "approval_requires_run_id",
                },
                error="approval_requires_run_id",
            )

        import uuid as _uuid
        from app.application.monitoring import runtime as _mon

        _mon.expire_old_approvals()
        existing = _mon.find_approved_approval(
            tool_name=tool_name,
            agent_id=request.agent_id,
            source=request.source,
            run_id=request.run_id,
            project_scope_id=request.project_scope_id,
            args=request.args,
        )
        if existing:
            _mon.update_approval_status(existing["id"], status="used")
        else:
            approval = _mon.create_approval(
                id=_uuid.uuid4().hex,
                tool_name=tool_name,
                agent_id=request.agent_id,
                source=request.source,
                run_id=request.run_id,
                project_scope_id=request.project_scope_id,
                args=request.args,
            )
            _emit_approval_pending(request, approval["id"])
            # Local Tauri approvals stay local. Only a run that the Telegram poller
            # server-side marks with agent_id="telegram" may notify Telegram.
            if request.agent_id == "telegram" and tool_name != "itops_change_apply":
                try:
                    from app.application.telegram.runtime import send_approval_notification
                    send_approval_notification(approval)
                except Exception as exc:
                    logger.debug("Telegram approval notification failed", exc_info=exc)
            return ToolExecutionResult(
                status="waiting_approval",
                output={
                    "ok": False,
                    "approval_id": approval["id"],
                    # Model-oriented wording: with the F1 pause this text only
                    # reaches the model on wait-timeout (or when the caller
                    # opted out of pausing) — it must instruct, not link.
                    "text": (
                        f"Действие '{tool_name}' ожидает подтверждения пользователя. "
                        "Не повторяй вызов с теми же аргументами; дождись итога "
                        "или сообщи пользователю."
                    ),
                    "error": f"waiting_approval:{approval['id']}",
                },
                error=f"waiting_approval:{approval['id']}",
            )

    # 4. Dispatch with a HARD per-class deadline. A synchronous in-process tool
    # (e.g. a pure-Python grep over a huge tree) cannot be interrupted — a Python
    # thread does not respond to a kill — so we run dispatch_fn in a daemon worker
    # and join with the tool's budget. On overrun the kernel STOPS WAITING and
    # returns a timeout error: the abandoned worker keeps running (daemon, dies
    # with the process), but the caller's run lifecycle proceeds to its `finally`,
    # which releases the global write-lock. Without this an unbounded tool froze
    # every future run by holding .agent/agent.lock forever.
    import threading as _threading
    import time as _time

    _budget = _resolve_tool_timeout(spec, tool_name)
    _result_box: dict[str, Any] = {}

    def _runner() -> None:
        # Bind run_id on THIS worker thread so a cancellable tool (run_bash)
        # can register its live OS process against the run. The Stop route then
        # reaches in and kills it directly — a daemon worker thread cannot be
        # interrupted otherwise. Best-effort: tools that don't use it are
        # unaffected, and a missing helper must never break dispatch.
        _run_token = None
        _channel_token = None
        try:
            from app.application.code_agent.tools import set_current_run_id
            _run_token = set_current_run_id(request.run_id)
        except Exception:
            _run_token = None
        try:
            from app.application.agent_kernel.execution_context import set_execution_channel
            _channel_token = set_execution_channel(
                "remote" if request.agent_id == "telegram" else "local"
            )
            _result_box["raw"] = dispatch_fn(tool_name, request.args)
        except Exception as exc:  # noqa: BLE001 — surfaced to the model as tool error
            _result_box["raw"] = {"ok": False, "text": f"ERROR: {exc}", "error": str(exc)}
        finally:
            if _channel_token is not None:
                try:
                    from app.application.agent_kernel.execution_context import reset_execution_channel
                    reset_execution_channel(_channel_token)
                except Exception:
                    pass
            if _run_token is not None:
                try:
                    from app.application.code_agent.tools import reset_current_run_id
                    reset_current_run_id(_run_token)
                except Exception:
                    pass

    _t0 = _time.monotonic()
    _worker = _threading.Thread(
        target=_runner, name=f"tool-exec:{tool_name}", daemon=True
    )
    _worker.start()
    _worker.join(_budget)
    _elapsed = _time.monotonic() - _t0

    if _worker.is_alive():
        # Hard timeout: abandon the worker, fail the call, let the run release
        # its lock. The daemon thread is intentionally not joined further.
        _emit_timeout(request, _elapsed, _budget)
        logger.warning(
            "tool %s exceeded hard timeout %ss (run=%s) — abandoning worker, failing call",
            tool_name, _budget, request.run_id,
        )
        _err = f"tool_timeout:{tool_name} exceeded {_budget}s"
        _to_text = (
            f"Инструмент '{tool_name}' превысил жёсткий лимит {_budget}s и был прерван. "
            "Выполнение помечено как ошибка; попробуй сузить запрос (например, путь/паттерн) "
            "или вызвать инструмент иначе."
        )
        _emit_executed(request, {"ok": False, "error": _err}, "error")
        return ToolExecutionResult(
            status="error",
            output={"ok": False, "text": _to_text, "error": _err},
            error=_err,
        )

    raw = _result_box.get("raw", {"ok": False, "text": "ERROR: tool returned no result", "error": "no_result"})

    if not isinstance(raw, dict):
        raw = {"text": str(raw)}

    # Normalise: callers expect a "text" key for LLM feedback
    if "text" not in raw:
        import json as _json
        raw = {**raw, "text": _json.dumps(raw, ensure_ascii=False)}

    # 5. Truncate text output
    text = raw.get("text", "")
    if isinstance(text, str) and len(text) > max_chars:
        raw = {**raw, "text": text[:max_chars] + _TRUNCATION_SUFFIX}

    # 6. Emit audit event
    status = "ok" if raw.get("ok", True) else "error"
    _emit_executed(request, raw, status)

    return ToolExecutionResult(
        status=status,
        output=raw,
        error=raw.get("error") if status == "error" else None,
    )


def _emit_executed(req: ToolExecutionRequest, result: dict, status: str) -> None:
    try:
        from app.application.event_bus import runtime as _eb
        _eb.emit_event(
            event_type="tool.executed",
            payload={
                "tool_name": req.tool_name,
                "agent_id": req.agent_id,
                "source": req.source,
                "project_scope_id": req.project_scope_id,
                "run_id": req.run_id,
                "workflow_id": req.workflow_id,
                "step_id": req.step_id,
                "status": status,
                "ok": result.get("ok", True),
                "success": result.get("ok", True),
                "error": result.get("error"),
            },
        )
    except Exception as exc:
        logger.debug("tool.executed event emission failed", exc_info=exc)


def _emit_approval_pending(req: ToolExecutionRequest, approval_id: str) -> None:
    try:
        from app.application.event_bus import runtime as _eb
        _eb.emit_event(
            event_type="tool.approval_pending",
            payload={
                "tool_name": req.tool_name,
                "agent_id": req.agent_id,
                "source": req.source,
                "project_scope_id": req.project_scope_id,
                "run_id": req.run_id,
                "approval_id": approval_id,
            },
        )
    except Exception as exc:
        logger.debug("tool.approval_pending event emission failed", exc_info=exc)


# Per-class hard-timeout floors (seconds). A tool's effective budget is the
# MAX of its class floor and any explicit, larger spec.timeout_seconds — so the
# stock spec default (30, a model-request hint, never the execution budget)
# never wrongly kills a legitimately slow shell/network tool, while an admin can
# still RAISE a specific tool's budget via the ToolSpec.
_TIMEOUT_CLASS_LOCAL = 120     # fs.read/fs.write/recall and other in-process tools
_TIMEOUT_CLASS_SHELL = 600     # shell.exec — commands/builds
_TIMEOUT_CLASS_NETWORK = 900   # net.outbound — web_fetch / ssh / remote monitoring


def _resolve_tool_timeout(spec: dict[str, Any] | None, tool_name: str) -> int:
    """Hard execution budget for one tool call, by declared scope class.

    Classification is by the tool's *declared scopes* (authoritative, set at
    seed time), not a guessed name list: net.outbound → network class (longest,
    covers remote machines that legitimately take 10-15 min), shell.exec → shell
    class, everything else → local class. spec.timeout_seconds only raises the
    floor, never lowers it.
    """
    spec = spec or {}
    scopes = set(spec.get("scopes") or [])
    if "net.outbound" in scopes:
        floor = _TIMEOUT_CLASS_NETWORK
    elif "shell.exec" in scopes:
        floor = _TIMEOUT_CLASS_SHELL
    else:
        floor = _TIMEOUT_CLASS_LOCAL
    try:
        explicit = int(spec.get("timeout_seconds") or 0)
    except (TypeError, ValueError):
        explicit = 0
    return max(floor, explicit)


def _emit_timeout(req: ToolExecutionRequest, elapsed: float, budget: int) -> None:
    try:
        from app.application.event_bus import runtime as _eb
        _eb.emit_event(
            event_type="tool.timeout",
            payload={
                "tool_name": req.tool_name,
                "agent_id": req.agent_id,
                "source": req.source,
                "run_id": req.run_id,
                "elapsed_seconds": round(elapsed, 3),
                "budget_seconds": budget,
                "observed": True,
            },
        )
    except Exception as exc:
        logger.debug("tool.timeout event emission failed", exc_info=exc)


def _emit_invalid_spec(req: ToolExecutionRequest, reason: str) -> None:
    """Audit a fail-closed block caused by a missing/invalid/unclassified ToolSpec.

    Distinct from sandbox.policy.blocked (a policy decision on a valid spec): this
    flags an integrity problem with the spec itself — the tool never reaches dispatch.
    """
    try:
        from app.application.event_bus import runtime as _eb
        from app.core.redaction import redact_secrets as _redact
        _eb.emit_event(
            event_type="tool.invalid_spec",
            payload={
                "tool_name": req.tool_name,
                "agent_id": req.agent_id,
                "source": req.source,
                "project_scope_id": req.project_scope_id,
                "run_id": req.run_id,
                "reason": _redact(reason),
            },
        )
    except Exception as exc:
        logger.debug("tool.invalid_spec event emission failed", exc_info=exc)


def _emit_blocked(req: ToolExecutionRequest, reason: str) -> None:
    try:
        from app.application.event_bus import runtime as _eb
        from app.core.redaction import redact_secrets as _redact
        _eb.emit_event(
            event_type="sandbox.policy.blocked",
            payload={
                "tool_name": req.tool_name,
                "agent_id": req.agent_id,
                "source": req.source,
                "project_scope_id": req.project_scope_id,
                "run_id": req.run_id,
                "reason": _redact(reason),
            },
        )
    except Exception as exc:
        logger.debug("sandbox.policy.blocked event emission failed", exc_info=exc)
