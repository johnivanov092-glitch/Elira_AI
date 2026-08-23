from __future__ import annotations

import os
from typing import Any

# Offline fallback for injected/scripted runtimes. Live model runs use /props.
DEFAULT_CONTEXT_WINDOW = 131_072

_COMPACT_AUTO_PCT = 75.0
_COMPACT_STRONG_PCT = 90.0
_COMPACT_CRITICAL_PCT = 95.0
_SMALL_WINDOW_TOKENS = 16_384
_SMALL_AUTO_PCT = 60.0
_SMALL_STRONG_PCT = 80.0
_SMALL_CRITICAL_PCT = 95.0


class ContextResolutionError(RuntimeError):
    """The live server did not provide a trustworthy context window."""


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _fmt_k(value: int) -> str:
    return f"{max(1, int(value) // 1024)}K"


def _reserves(effective: int, *, thinking: bool) -> tuple[int, int, int]:
    """Return output/system/safety reserves proportional to the active window."""
    if effective < _SMALL_WINDOW_TOKENS:
        reserved_output = max(512, effective // 8)
        reserved_system = max(512, effective // 8)
        safety_margin = max(256, effective // 16)
    else:
        reserved_output = max(2048, effective // 16)
        reserved_system = max(2048, effective // 32)
        safety_margin = max(2048, effective // 64)
    if thinking:
        reserved_output = min(
            max(reserved_output * 2, effective // 8),
            max(2048, effective // 3),
        )
    return reserved_output, reserved_system, safety_margin


def _thresholds(effective: int) -> dict[str, dict[str, float | int]]:
    small = effective < _SMALL_WINDOW_TOKENS
    auto_pct = _SMALL_AUTO_PCT if small else _COMPACT_AUTO_PCT
    strong_pct = _SMALL_STRONG_PCT if small else _COMPACT_STRONG_PCT
    critical_pct = _SMALL_CRITICAL_PCT if small else _COMPACT_CRITICAL_PCT
    return {
        "auto": {"percent": auto_pct, "tokens": int(effective * auto_pct / 100)},
        "strong": {"percent": strong_pct, "tokens": int(effective * strong_pct / 100)},
        "critical": {
            "percent": critical_pct,
            "tokens": int(effective * critical_pct / 100),
        },
    }


def resolve_context_window(
    offline_context_window: int | None = None,
    *,
    model: str = "local-model",
    thinking: bool = False,
    live: bool = True,
    fresh: bool = True,
) -> dict[str, Any]:
    """Resolve one authoritative context profile.

    Live runs use the positive llama.cpp ``/props`` ``n_ctx`` verbatim. The
    legacy request value, frontend state, model profile, and monitoring limits
    cannot shrink or enlarge it. Injected runtimes without a server use the
    explicit offline value, then environment/config/default fallbacks.
    """
    from app.infrastructure.llm.openai_compatible import (
        local_llm_config,
        server_context_window,
    )

    cfg = local_llm_config()
    env_window = _positive_int(os.getenv("LLAMA_SERVER_CONTEXT_WINDOW"))

    server_ctx: int | None = None
    source = "offline"
    if live:
        server_ctx = server_context_window(fresh=fresh)
        if server_ctx:
            source = "server_props"
        else:
            raise ContextResolutionError(
                "context window unavailable: llama.cpp /props did not return a "
                "positive n_ctx; refusing to guess the live server window"
            )
    if not server_ctx:
        server_ctx = (
            _positive_int(offline_context_window)
            or env_window
            or _positive_int(cfg.context_window)
            or DEFAULT_CONTEXT_WINDOW
        )
    if server_ctx < 1024:
        raise ContextResolutionError(
            f"context window reported by {source} is too small: {server_ctx}"
        )

    effective = server_ctx
    reserved_output, reserved_system, safety_margin = _reserves(
        effective,
        thinking=thinking,
    )
    thresholds = _thresholds(effective)
    return {
        "active_model": model or cfg.model,
        "model_alias": cfg.model,
        "main_endpoint": cfg.base_url,
        "ctx_size": effective,
        "mode": _fmt_k(effective).lower(),
        "reserved_output_tokens": reserved_output,
        "reserved_system_tokens": reserved_system,
        "safety_margin_tokens": safety_margin,
        "safe_input_budget": max(
            1024,
            effective - reserved_output - reserved_system - safety_margin,
        ),
        "compaction_thresholds": thresholds,
        "source": source,
        "thinking": bool(thinking),
        "requested_context_mode": "server" if live else "offline",
        "requested_context_cap": None,
        "server_context_window": server_ctx,
        "effective_context_window": effective,
        "limiting_source": "server" if source == "server_props" else source,
        "context_profile_source": source,
    }


def get_active_context_profile(
    model: str = "local-model",
    *,
    ctx_size: int | None = None,
    thinking: bool = False,
    fresh: bool = False,
) -> dict[str, Any]:
    """Compatibility wrapper for passive profile consumers.

    Passive callers may use the short transport cache. If the server is not
    reachable, this read-only helper degrades to the offline/config profile.
    Model-call paths use ``resolve_context_window(..., live=True, fresh=True)``
    directly and therefore fail closed.
    """
    try:
        return resolve_context_window(
            None,
            model=model,
            thinking=thinking,
            live=True,
            fresh=fresh,
        )
    except ContextResolutionError:
        return resolve_context_window(
            _positive_int(ctx_size),
            model=model,
            thinking=thinking,
            live=False,
        )
