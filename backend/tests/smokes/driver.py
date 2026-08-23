# -*- coding: utf-8 -*-
"""SSE driver for live smokes: POST /api/code-agent/stream, consume the stream,
return a structured summary. stdlib-only (urllib), no test-framework coupling —
this is NOT collected by pytest (live runs take minutes and need a running
backend + LLM); the entry point is tests/smokes/run.py."""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any


_MODEL_ACTION_EVENTS = frozenset({
    "reasoning_delta", "delta", "tool_started", "final_response",
})
_TOKEN_EVENTS = frozenset({"reasoning_delta", "delta"})
_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+")


def _extract_urls(value: object) -> list[str]:
    return list(dict.fromkeys(
        match.group(0).rstrip(".,;:")
        for match in _URL_RE.finditer(str(value or ""))
    ))


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
        float(event.get("tokens_per_second") or 0.0)
        for event in usage_events
        if float(event.get("tokens_per_second") or 0.0) > 0
    ]
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
        tool_trace.append(trace_item)
        if tool_name == "runtime_control" and operation:
            runtime_calls.append({
                "operation": operation,
                **({"server_id": server_id} if server_id else {}),
                "ok": event.get("ok") is True,
            })
        source_urls = _extract_urls(event.get("result"))
        if source_urls:
            tool_source_urls.setdefault(tool_name, [])
            tool_source_urls[tool_name] = list(dict.fromkeys(
                [*tool_source_urls[tool_name], *source_urls]
            ))[:50]
        if tool_name == "itops_network_inventory":
            network_inventories.append(_network_inventory_observation(event))

    answer = str(final.get("text") or "")[:4000]
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
        "completion_status": done.get("completion_status"),
        "confirmed": sum(1 for criterion in criteria if criterion.get("status") == "confirmed"),
        "total_criteria": len(criteria),
        "criteria": [
            {"status": criterion.get("status"), "text": (criterion.get("text") or "")[:70]}
            for criterion in criteria
        ],
        "tool_calls": len(tool_calls),
        "tool_names": [str(event.get("tool") or "") for event in tool_calls],
        "tool_trace": tool_trace,
        "runtime_calls": runtime_calls,
        "runtime_operations": [call["operation"] for call in runtime_calls],
        "tool_source_urls": tool_source_urls,
        "network_inventories": network_inventories,
        "auto_verifier_calls": sum(1 for event in tool_calls if event.get("auto_verifier")),
        "steps": sum(1 for event in events if event.get("type") == "step_started"),
        "prompt_tokens": sum(int(event.get("prompt_tokens") or 0) for event in usage_events),
        "completion_tokens": sum(
            int(event.get("completion_tokens") or 0) for event in usage_events
        ),
        "tokens_per_second": round(token_rates[-1], 1) if token_rates else 0.0,
        "answer": answer,
        "answer_urls": _extract_urls(answer),
        "server_ports": _server_ports(events),
    }


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
) -> dict[str, Any]:
    """Execute ONE smoke run and summarize it. Raises on transport errors;
    an in-run failure is reported via the summary (stop_reason/error)."""
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
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp, \
         open(events_path, "w", encoding="utf-8") as out:
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
            buf = b""

    return summarize_events(
        events,
        run_id=run_id,
        duration_s=time.monotonic() - t0,
        event_elapsed_s=event_elapsed_s,
    )


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
