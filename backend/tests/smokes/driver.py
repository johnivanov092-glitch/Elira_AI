# -*- coding: utf-8 -*-
"""SSE driver for live smokes: POST /api/code-agent/stream, consume the stream,
return a structured summary. stdlib-only (urllib), no test-framework coupling —
this is NOT collected by pytest (live runs take minutes and need a running
backend + LLM); the entry point is tests/smokes/run.py."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


_MODEL_ACTION_EVENTS = frozenset({
    "reasoning_delta", "delta", "tool_started", "final_response",
})
_TOKEN_EVENTS = frozenset({"reasoning_delta", "delta"})
_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+")
_WORKFLOW_REQUEST_KINDS = frozenset({"input", "secret", "elevation", "approval"})
_WORKFLOW_REQUEST_ACTIONS = frozenset({"accept", "decline", "cancel"})


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _extract_urls(value: object) -> list[str]:
    return list(dict.fromkeys(
        match.group(0).rstrip(".,;:")
        for match in _URL_RE.finditer(str(value or ""))
    ))


def _normalize_workflow_responses(
    responses: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(responses or []):
        if not isinstance(raw, dict):
            raise ValueError(f"workflow_responses[{index}] must be an object")
        kind = str(raw.get("kind") or "").strip().lower()
        action = str(raw.get("action") or "accept").strip().lower()
        values = raw.get("values") or {}
        message_contains = str(raw.get("message_contains") or "").strip()
        if kind not in _WORKFLOW_REQUEST_KINDS:
            raise ValueError(f"workflow_responses[{index}].kind is invalid: {kind}")
        if action not in _WORKFLOW_REQUEST_ACTIONS:
            raise ValueError(f"workflow_responses[{index}].action is invalid: {action}")
        if not isinstance(values, dict):
            raise ValueError(f"workflow_responses[{index}].values must be an object")
        if kind == "secret" and action == "accept":
            secret_ref = str(values.get("secret_ref") or "").strip()
            if set(values) != {"secret_ref"} or not secret_ref:
                raise ValueError(
                    "scripted secret acceptance requires only a non-empty secret_ref"
                )
        if kind == "elevation" and action == "accept":
            raise ValueError(
                "live Harness cannot fake elevation acceptance; use decline/cancel "
                "or resolve it through the native Tauri bridge"
            )
        normalized.append({
            "kind": kind,
            "action": action,
            "values": dict(values),
            "message_contains": message_contains,
        })
    return normalized


def _workflow_api_json(
    backend: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        backend.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError(f"Workflow API returned non-object JSON for {path}")
    return decoded


def _matching_workflow_run_id(
    backend: str,
    code_agent_run_id: str,
) -> str:
    payload = _workflow_api_json(
        backend,
        "/api/agent-os/workflow-runs?limit=100&offset=0",
    )
    for run in payload.get("runs") or []:
        if not isinstance(run, dict):
            continue
        context = run.get("context") or {}
        if (
            isinstance(context, dict)
            and str(context.get("code_agent_run_id") or "") == code_agent_run_id
        ):
            return str(run.get("run_id") or "")
    return ""


def _script_matches_request(script: dict[str, Any], request: dict[str, Any]) -> bool:
    if script["kind"] != str(request.get("kind") or "").strip().lower():
        return False
    fragment = str(script.get("message_contains") or "")
    return not fragment or fragment.casefold() in str(request.get("message") or "").casefold()


def _scripted_workflow_response_worker(
    *,
    backend: str,
    code_agent_run_id: str,
    scripts: list[dict[str, Any]],
    stop: threading.Event,
    resolved: list[dict[str, str]],
    errors: list[str],
    used_scripts: set[int],
) -> None:
    workflow_run_id = ""
    handled_request_ids: set[str] = set()
    while not stop.wait(0.2):
        try:
            if not workflow_run_id:
                workflow_run_id = _matching_workflow_run_id(backend, code_agent_run_id)
                if not workflow_run_id:
                    continue
            payload = _workflow_api_json(
                backend,
                f"/api/agent-os/workflow-runs/{workflow_run_id}/requests?limit=100&offset=0",
            )
        except Exception:
            continue
        for request in payload.get("requests") or []:
            if not isinstance(request, dict):
                continue
            request_id = str(request.get("request_id") or "")
            status = str(request.get("status") or "")
            if not request_id or request_id in handled_request_ids:
                continue
            if status not in {"pending", "needs_reconciliation"}:
                continue
            match = next(
                (
                    index
                    for index, script in enumerate(scripts)
                    if index not in used_scripts and _script_matches_request(script, request)
                ),
                None,
            )
            if match is None:
                errors.append(
                    "no scripted Workflow response for "
                    f"{request.get('kind')} request {request_id}: {request.get('message', '')}"
                )
                action = "cancel"
                values: dict[str, Any] = {}
            else:
                script = scripts[match]
                used_scripts.add(match)
                action = str(script["action"])
                values = dict(script["values"])
            try:
                _workflow_api_json(
                    backend,
                    f"/api/agent-os/workflow-requests/{request_id}/resolve",
                    method="POST",
                    payload={"action": action, "values": values},
                )
            except Exception as exc:
                errors.append(f"Workflow response failed for {request_id}: {exc}")
            else:
                resolved.append({
                    "request_id": request_id,
                    "kind": str(request.get("kind") or ""),
                    "action": action,
                })
            handled_request_ids.add(request_id)


def _network_inventory_observation(event: dict[str, Any]) -> dict[str, Any]:
    arguments = event.get("arguments") or {}
    result = str(event.get("result") or "")
    open_ports: set[int] = set()
    if "open_endpoints=" in result:
        endpoints = result.split("open_endpoints=", 1)[1].split(";", 1)[0]
        open_ports.update(
            int(port)
            for port in re.findall(r"(?:^|,)[^,\s:]+:(\d+)", endpoints)
        )
    return {
        "cidr": str(arguments.get("cidr") or ""),
        "requested_ports": [int(port) for port in arguments.get("ports") or []],
        "open_ports": sorted(open_ports),
        "ok": event.get("ok") is True,
    }


def summarize_events(
    events: list[dict[str, Any]],
    *,
    run_id: str,
    duration_s: float,
    event_elapsed_s: list[float] | None = None,
) -> dict[str, Any]:
    """Summarize the public SSE contract emitted by one real agent run."""
    elapsed = event_elapsed_s or []
    done = next((event for event in reversed(events) if event.get("type") == "done"), {})
    started = next((event for event in events if event.get("type") == "run_started"), {})
    tool_calls = [event for event in events if event.get("type") == "tool_call"]
    usage_events = [event for event in events if event.get("type") == "usage"]
    final = next(
        (event for event in reversed(events) if event.get("type") == "final_response"),
        {},
    )

    first_action_s = None
    ttft_s = None
    activated_mcp: set[str] = set()
    final_runtime_activation = dict(started.get("runtime_activation") or {})
    for index, event in enumerate(events):
        event_type = str(event.get("type") or "")
        event_elapsed = elapsed[index] if index < len(elapsed) else None
        if first_action_s is None and event_type in _MODEL_ACTION_EVENTS:
            first_action_s = event_elapsed
        if ttft_s is None and event_type in _TOKEN_EVENTS:
            ttft_s = event_elapsed
        activation = event.get("runtime_activation")
        if isinstance(activation, dict):
            final_runtime_activation = dict(activation)
            activated_mcp.update(
                str(server_id)
                for server_id in activation.get("mcp_server_ids") or []
                if str(server_id).strip()
            )

    criteria = done.get("criteria") or []
    token_rates = [
        rate for event in usage_events
        if (rate := _as_float(event.get("tokens_per_second"))) is not None and rate > 0
    ]
    prompt_token_rates = [
        rate for event in usage_events
        if (rate := _as_float(event.get("prompt_tokens_per_second"))) is not None and rate > 0
    ]
    prompt_tokens = sum(
        _as_int(event.get("prompt_tokens")) or 0 for event in usage_events
    )
    cached_values = [_as_int(event.get("cached_prompt_tokens")) for event in usage_events]
    cached_prompt_tokens = sum(value or 0 for value in cached_values)
    has_numeric_cached_tokens = any(value is not None for value in cached_values)
    reported_cache_ratios = [
        (ratio, _as_int(event.get("prompt_tokens")) or 0)
        for event in usage_events
        if (ratio := _as_float(event.get("cache_hit_ratio"))) is not None
    ]
    if has_numeric_cached_tokens:
        cache_hit_ratio = cached_prompt_tokens / prompt_tokens if prompt_tokens else 0.0
    else:
        weighted_prompt_tokens = sum(weight for _, weight in reported_cache_ratios)
        cache_hit_ratio = (
            sum(ratio * weight for ratio, weight in reported_cache_ratios)
            / weighted_prompt_tokens
            if weighted_prompt_tokens
            else 0.0
        )
    model_ttft_ms = next(
        (
            value
            for event in usage_events
            if (value := _as_int(event.get("ttft_ms"))) is not None and value > 0
        ),
        0,
    )
    max_model_ttft_ms = max(
        (_as_int(event.get("ttft_ms")) or 0 for event in usage_events),
        default=0,
    )
    tool_trace: list[dict[str, Any]] = []
    runtime_calls: list[dict[str, Any]] = []
    tool_source_urls: dict[str, list[str]] = {}
    network_inventories: list[dict[str, Any]] = []
    for event in tool_calls:
        tool_name = str(event.get("tool") or "")
        arguments = event.get("arguments") or {}
        trace_item: dict[str, Any] = {
            "tool": tool_name,
            "ok": event.get("ok") is True,
        }
        operation = str(arguments.get("operation") or "")
        server_id = str(arguments.get("server_id") or "")
        if operation:
            trace_item["operation"] = operation
        if server_id:
            trace_item["server_id"] = server_id
        for key in ("action", "kind"):
            value = str(arguments.get(key) or "")
            if value:
                trace_item[key] = value
        status = str(event.get("status") or "")
        if status:
            trace_item["status"] = status
        tool_trace.append(trace_item)
        if tool_name == "runtime_control" and operation:
            runtime_calls.append({
                "operation": operation,
                **({"server_id": server_id} if server_id else {}),
                "ok": event.get("ok") is True,
            })
        if isinstance(event.get("sources"), list):
            source_urls = list(dict.fromkeys(
                source["url"] for source in event["sources"]
                if isinstance(source, dict) and source.get("status") in {"discovered", "fetched", "excerpt"}
                and isinstance(source.get("url"), str)
            ))
        else:
            source_urls = _extract_urls(event.get("result")) if event.get("ok") is True else []
        if source_urls:
            tool_source_urls.setdefault(tool_name, [])
            tool_source_urls[tool_name] = list(dict.fromkeys(
                [*tool_source_urls[tool_name], *source_urls]
            ))[:50]
        if tool_name == "itops_network_inventory":
            network_inventories.append(_network_inventory_observation(event))

    answer = str(final.get("text") or "")[:4000]
    tool_counts = Counter(str(event.get("tool") or "") for event in tool_calls)
    failed_tool_calls = sum(1 for event in tool_calls if event.get("ok") is not True)
    error_prefixed_successes = sum(
        1
        for event in tool_calls
        if event.get("ok") is True
        and str(event.get("result") or "").lstrip().startswith("ERROR:")
    )
    context_current_tokens = [
        _as_int((event.get("context") or {}).get("current_tokens")) or 0
        for event in usage_events
    ]
    context_tool_tokens = [
        _as_int(((event.get("context") or {}).get("breakdown") or {}).get("tools")) or 0
        for event in usage_events
    ]
    return {
        "run_id": run_id,
        "duration_s": round(float(duration_s), 3),
        "first_action_s": first_action_s,
        "ttft_s": ttft_s,
        "effective_profile": str(started.get("profile_name") or ""),
        "initial_runtime_activation": dict(started.get("runtime_activation") or {}),
        "final_runtime_activation": final_runtime_activation,
        "activated_mcp_server_ids": sorted(activated_mcp),
        "stop_reason": done.get("stop_reason"),
        "error": done.get("error"),
        "answer_status": done.get("answer_status"),
        "completion_status": done.get("completion_status"),
        "partial": bool(done.get("partial")),
        "resumable": bool(done.get("resumable")),
        "confirmed": sum(1 for criterion in criteria if criterion.get("status") == "confirmed"),
        "total_criteria": len(criteria),
        "criteria": [
            {"status": criterion.get("status"), "text": (criterion.get("text") or "")[:70]}
            for criterion in criteria
        ],
        "tool_calls": len(tool_calls),
        "tool_names": [str(event.get("tool") or "") for event in tool_calls],
        "tool_counts": dict(tool_counts),
        "failed_tool_calls": failed_tool_calls,
        "error_prefixed_successes": error_prefixed_successes,
        "tool_trace": tool_trace,
        "runtime_calls": runtime_calls,
        "runtime_operations": [call["operation"] for call in runtime_calls],
        "tool_source_urls": tool_source_urls,
        # URL availability is not entailment. Keep runtime provenance separate
        # from the semantic rubric; never upgrade a linked answer to true.
        "citations": list(final.get("citations") or []),
        "source_status": str(final.get("source_status") or "none"),
        "claim_support": "not_assessed",
        "network_inventories": network_inventories,
        "auto_verifier_calls": sum(1 for event in tool_calls if event.get("auto_verifier")),
        "steps": sum(1 for event in events if event.get("type") == "step_started"),
        "prompt_tokens": prompt_tokens,
        "cached_prompt_tokens": cached_prompt_tokens,
        "cached_prompt_tokens_available": has_numeric_cached_tokens,
        "cache_hit_ratio": round(cache_hit_ratio, 4),
        "completion_tokens": sum(
            _as_int(event.get("completion_tokens")) or 0 for event in usage_events
        ),
        "prompt_tokens_per_second": (
            round(prompt_token_rates[-1], 1) if prompt_token_rates else 0.0
        ),
        "tokens_per_second": round(token_rates[-1], 1) if token_rates else 0.0,
        "model_ttft_ms": model_ttft_ms,
        "max_model_ttft_ms": max_model_ttft_ms,
        "max_context_tokens": max(context_current_tokens, default=0),
        "final_context_tokens": context_current_tokens[-1] if context_current_tokens else 0,
        "max_tool_context_tokens": max(context_tool_tokens, default=0),
        "final_tool_context_tokens": context_tool_tokens[-1] if context_tool_tokens else 0,
        "answer": answer,
        "answer_urls": _extract_urls(answer),
        "server_ports": _server_ports(events),
    }


def summarize_journal(run_dir: Path) -> dict[str, Any]:
    """Replay one durable run journal through the same Harness reporter.

    Old journals may contain redacted token metrics; ``summarize_events`` keeps
    those reports usable and falls back to persisted cache ratios without
    pretending the unavailable absolute cached-token count is known.
    """
    events_path = run_dir / "events.jsonl"
    state_path = run_dir / "state.json"
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.is_file()
        else {}
    )
    timestamps: list[datetime | None] = []
    for event in events:
        raw = str(event.get("timestamp") or "").strip().replace("Z", "+00:00")
        try:
            timestamps.append(datetime.fromisoformat(raw) if raw else None)
        except ValueError:
            timestamps.append(None)
    started = next((value for value in timestamps if value is not None), None)
    elapsed = [
        round((value - started).total_seconds(), 3)
        if value is not None and started is not None
        else 0.0
        for value in timestamps
    ]
    duration_s = elapsed[-1] if elapsed else 0.0
    summary = summarize_events(
        events,
        run_id=str(state.get("run_id") or run_dir.name),
        duration_s=duration_s,
        event_elapsed_s=elapsed,
    )
    request = state.get("request") or {}
    summary.update({
        "state_status": state.get("status"),
        "state_completion_status": state.get("completion_status"),
        "state_resumable": bool(state.get("resumable")),
        "project_root": str(state.get("project_root") or request.get("project_root") or ""),
        "user_message": str(request.get("user_message") or state.get("task") or "")[:1000],
    })
    return summary


def run_smoke(
    *,
    backend: str,
    task_text: str,
    project_root: str,
    run_id: str,
    events_path: Path,
    timeout_s: float | None = 1200,
    profile_name: str = "Авто",
    reasoning_effort: str = "low",
    permission_mode: str = "bypass",
    workflow_responses: list[dict[str, Any]] | None = None,
    cancel_on_tool: str = "",
    resume_after_stop: bool = False,
) -> dict[str, Any]:
    """Execute ONE smoke run and summarize it. Raises on transport errors;
    an in-run failure is reported via the summary (stop_reason/error)."""
    scripts = _normalize_workflow_responses(workflow_responses)
    body = json.dumps({
        "message": task_text,
        "project_root": project_root,
        "run_id": run_id,
        "profile_name": profile_name,
        "permission_mode": permission_mode,
        "reasoning_effort": reasoning_effort,
    }).encode("utf-8")
    req = urllib.request.Request(
        backend.rstrip("/") + "/api/code-agent/stream",
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    )

    t0 = time.monotonic()
    events: list[dict] = []
    event_elapsed_s: list[float] = []
    resolved_requests: list[dict[str, str]] = []
    workflow_response_errors: list[str] = []
    used_workflow_scripts: set[int] = set()
    workflow_stop_requested = False
    workflow_stop_error = ""
    workflow_resume_attempted = False
    workflow_resume_error = ""
    pre_resume_stop_reason = ""
    responder_stop = threading.Event()
    responder = None
    if scripts:
        responder = threading.Thread(
            target=_scripted_workflow_response_worker,
            kwargs={
                "backend": backend,
                "code_agent_run_id": run_id,
                "scripts": scripts,
                "stop": responder_stop,
                "resolved": resolved_requests,
                "errors": workflow_response_errors,
                "used_scripts": used_workflow_scripts,
            },
            name=f"eval-workflow-responder-{run_id}",
            daemon=True,
        )
        responder.start()
    events_path.parent.mkdir(parents=True, exist_ok=True)

    def consume_sse(request: urllib.request.Request, out: Any) -> None:
        nonlocal workflow_stop_requested, workflow_stop_error
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            buf = b""
            while True:
                chunk = resp.read(1)
                if not chunk:
                    break
                buf += chunk
                if not buf.endswith(b"\n\n"):
                    continue
                for line in buf.decode("utf-8", "replace").splitlines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        ev = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    events.append(ev)
                    event_elapsed_s.append(round(time.monotonic() - t0, 3))
                    out.write(json.dumps(ev, ensure_ascii=False) + "\n")
                    out.flush()
                    if (
                        cancel_on_tool
                        and not workflow_stop_requested
                        and ev.get("type") == "tool_started"
                        and str(ev.get("tool") or "") == cancel_on_tool
                    ):
                        try:
                            _workflow_api_json(
                                backend,
                                "/api/code-agent/cancel",
                                method="POST",
                                payload={"run_id": run_id},
                            )
                        except Exception as exc:
                            workflow_stop_error = str(exc)
                        else:
                            workflow_stop_requested = True
                buf = b""

    try:
        with open(events_path, "w", encoding="utf-8") as out:
            consume_sse(req, out)
            if resume_after_stop:
                pre_resume_stop_reason = str(
                    next(
                        (event for event in reversed(events) if event.get("type") == "done"),
                        {},
                    ).get("stop_reason") or ""
                )
                workflow_resume_attempted = True
                resume_request = urllib.request.Request(
                    backend.rstrip("/") + f"/api/code-agent/runs/{run_id}/resume",
                    data=b"",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    consume_sse(resume_request, out)
                except Exception as exc:
                    workflow_resume_error = str(exc)
    finally:
        responder_stop.set()
        if responder is not None:
            responder.join(timeout=5)

    summary = summarize_events(
        events,
        run_id=run_id,
        duration_s=time.monotonic() - t0,
        event_elapsed_s=event_elapsed_s,
    )
    summary["workflow_requests_resolved"] = resolved_requests
    summary["workflow_response_errors"] = workflow_response_errors
    summary["workflow_responses_unused"] = len(scripts) - len(used_workflow_scripts)
    summary["workflow_stop_requested"] = workflow_stop_requested
    summary["workflow_stop_error"] = workflow_stop_error
    summary["workflow_resume_attempted"] = workflow_resume_attempted
    summary["workflow_resume_error"] = workflow_resume_error
    summary["pre_resume_stop_reason"] = pre_resume_stop_reason
    return summary


def _server_ports(events: list[dict]) -> list[int]:
    """Loopback ports any dev server announced during the run (for cleanup checks)."""
    ports: set[int] = set()
    for ev in events:
        for key in ("result", "text"):
            s = str(ev.get(key) or "")
            for tok in s.split():
                if "localhost:" in tok:
                    tail = tok.split("localhost:", 1)[1]
                    digits = ""
                    for ch in tail:
                        if ch.isdigit():
                            digits += ch
                        else:
                            break
                    if digits:
                        ports.add(int(digits))
    return sorted(p for p in ports if p != 8000)   # exclude the backend itself
