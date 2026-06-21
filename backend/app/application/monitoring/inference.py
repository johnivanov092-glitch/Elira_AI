from __future__ import annotations

from typing import Any


def _get_field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _duration_ns_to_ms(value: Any) -> int:
    raw = _as_int(value)
    if raw <= 0:
        return 0
    return max(1, int(raw / 1_000_000))


def extract_llm_usage(response: Any) -> dict[str, Any]:
    """Extract token and latency usage fields from a local LLM response."""
    prompt_tokens = _as_int(_get_field(response, "prompt_eval_count"))
    completion_tokens = _as_int(_get_field(response, "eval_count"))
    total_tokens = prompt_tokens + completion_tokens
    total_duration_ms = _duration_ns_to_ms(_get_field(response, "total_duration"))
    prompt_duration_ms = _duration_ns_to_ms(_get_field(response, "prompt_eval_duration"))
    completion_duration_ms = _duration_ns_to_ms(_get_field(response, "eval_duration"))

    tokens_per_second = 0.0
    eval_duration_ns = _as_float(_get_field(response, "eval_duration"))
    if completion_tokens > 0 and eval_duration_ns > 0:
        tokens_per_second = completion_tokens / (eval_duration_ns / 1_000_000_000)

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "latency_ms": total_duration_ms,
        "total_duration_ms": total_duration_ms,
        "prompt_duration_ms": prompt_duration_ms,
        "completion_duration_ms": completion_duration_ms,
        "tokens_per_second": tokens_per_second,
    }


def _record_resource(
    *,
    agent_id: str,
    run_id: str,
    resource: str,
    amount: float,
    unit: str,
    details: dict[str, Any],
) -> None:
    if amount <= 0:
        return
    try:
        from app.application.monitoring.runtime import record_resource_usage

        record_resource_usage(
            agent_id=agent_id,
            run_id=run_id,
            resource=resource,
            amount=amount,
            unit=unit,
            details=details,
        )
    except Exception:
        pass


def record_inference_telemetry(
    *,
    agent_id: str,
    run_id: str,
    route: str,
    model: str,
    provider: str = "",
    profile_id: str = "",
    role: str = "",
    routing_source: str = "",
    requested_model: str = "",
    num_ctx: int = 0,
    ok: bool,
    duration_ms: int = 0,
    streaming: bool = False,
    usage: dict[str, Any] | None = None,
    prompt_chars: int = 0,
    completion_chars: int = 0,
    tool_round_trips: int = 0,
    compaction_count: int = 0,
    fallback_count: int = 0,
    approval_wait_ms: int = 0,
    ttft_ms: int | None = None,
    error_category: str = "",
) -> None:
    """Best-effort LLM inference telemetry over existing agent_monitor tables."""
    safe_usage = dict(usage or {})
    prompt_tokens = _as_int(safe_usage.get("prompt_tokens"))
    completion_tokens = _as_int(safe_usage.get("completion_tokens"))
    total_tokens = _as_int(safe_usage.get("total_tokens")) or prompt_tokens + completion_tokens
    safe_num_ctx = max(0, _as_int(num_ctx))
    context_utilization = (total_tokens / safe_num_ctx) if safe_num_ctx and total_tokens else 0.0
    safe_duration_ms = max(0, _as_int(duration_ms))
    if safe_duration_ms <= 0:
        safe_duration_ms = max(
            0,
            _as_int(safe_usage.get("latency_ms"))
            or _as_int(safe_usage.get("total_duration_ms")),
        )
    safe_ttft_ms = None if ttft_ms is None else max(0, _as_int(ttft_ms))
    safe_tool_round_trips = max(0, _as_int(tool_round_trips))
    safe_compactions = max(0, _as_int(compaction_count))
    safe_fallbacks = max(0, _as_int(fallback_count))
    safe_approval_wait_ms = max(0, _as_int(approval_wait_ms))
    tokens_per_second = _as_float(safe_usage.get("tokens_per_second"))

    details = {
        "provider": str(provider or ""),
        "model": str(model or ""),
        "profile_id": str(profile_id or ""),
        "role": str(role or ""),
        "route": str(route or ""),
        "routing_source": str(routing_source or ""),
        "requested_model": str(requested_model or ""),
        "streaming": bool(streaming),
        "num_ctx": safe_num_ctx,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "context_utilization": context_utilization,
        "prompt_chars": max(0, _as_int(prompt_chars)),
        "completion_chars": max(0, _as_int(completion_chars)),
        "tool_round_trips": safe_tool_round_trips,
        "compaction_count": safe_compactions,
        "fallback_count": safe_fallbacks,
        "approval_wait_ms": safe_approval_wait_ms,
        "ttft_ms": safe_ttft_ms,
        "tokens_per_second": tokens_per_second,
        "error_category": str(error_category or ""),
        "usage_available": bool(prompt_tokens or completion_tokens or total_tokens),
    }

    try:
        from app.application.monitoring.runtime import record_metric

        record_metric(
            metric_type="model.inference",
            agent_id=agent_id,
            run_id=run_id,
            ok=ok,
            duration_ms=safe_duration_ms,
            details=details,
        )
    except Exception:
        pass

    base_details = {
        "route": str(route or ""),
        "model": str(model or ""),
        "provider": str(provider or ""),
        "profile_id": str(profile_id or ""),
    }
    _record_resource(
        agent_id=agent_id,
        run_id=run_id,
        resource="llm_prompt_tokens",
        amount=prompt_tokens,
        unit="tokens",
        details=base_details,
    )
    _record_resource(
        agent_id=agent_id,
        run_id=run_id,
        resource="llm_completion_tokens",
        amount=completion_tokens,
        unit="tokens",
        details=base_details,
    )
    _record_resource(
        agent_id=agent_id,
        run_id=run_id,
        resource="llm_total_tokens",
        amount=total_tokens,
        unit="tokens",
        details=base_details,
    )
    _record_resource(
        agent_id=agent_id,
        run_id=run_id,
        resource="llm_context_utilization",
        amount=context_utilization,
        unit="ratio",
        details={**base_details, "num_ctx": safe_num_ctx},
    )
    _record_resource(
        agent_id=agent_id,
        run_id=run_id,
        resource="llm_latency",
        amount=safe_duration_ms,
        unit="ms",
        details=base_details,
    )
    _record_resource(
        agent_id=agent_id,
        run_id=run_id,
        resource="llm_tool_round_trips",
        amount=safe_tool_round_trips,
        unit="count",
        details=base_details,
    )
    _record_resource(
        agent_id=agent_id,
        run_id=run_id,
        resource="llm_compactions",
        amount=safe_compactions,
        unit="count",
        details=base_details,
    )
    _record_resource(
        agent_id=agent_id,
        run_id=run_id,
        resource="llm_fallbacks",
        amount=safe_fallbacks,
        unit="count",
        details=base_details,
    )
