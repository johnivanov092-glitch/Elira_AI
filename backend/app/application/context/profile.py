from __future__ import annotations

from typing import Any

from app.application.context.timeouts import TIMEOUT_POLICY_SECONDS

DEFAULT_CONTEXT_WINDOW = 131_072
MAX_CONTEXT_WINDOW = 262_144


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def get_active_context_profile(model: str = "local-model", *, ctx_size: int | None = None) -> dict[str, Any]:
    """Return the live llama.cpp context window with bounded config fallback."""
    from app.infrastructure.llm.openai_compatible import local_llm_config, list_models

    cfg = local_llm_config()
    context_window = _positive_int(ctx_size) or _positive_int(cfg.context_window) or DEFAULT_CONTEXT_WINDOW
    source = "effective_limit" if _positive_int(ctx_size) else "config"
    if ctx_size is None:
        try:
            models = list_models()
            # Prefer an exact name match; fall back to the first served model when
            # the caller passes an alias the server doesn't echo back (e.g. "auto",
            # the frontend default). llama.cpp serves a single model, so the first
            # entry's real n_ctx is authoritative — this is how the live 32k window
            # is adopted instead of the 128k config default.
            exact = next(
                (m for m in models if str(m.get("name") or m.get("model") or "").strip() == model),
                None,
            )
            chosen = exact or (models[0] if models else None)
            if chosen is not None:
                discovered = _positive_int(chosen.get("context_window") or chosen.get("n_ctx"))
                if discovered:
                    context_window = discovered
                    source = "server"
        except Exception:
            source = "config_fallback"

    context_window = min(MAX_CONTEXT_WINDOW, max(1024, context_window))
    if context_window < 16_384:
        # Small explicit windows still need room for prompt tokens; using the
        # large-window reserves would make the request fail before the chat
        # function is even called.
        reserved_output = max(512, context_window // 8)
        reserved_system = max(512, context_window // 8)
        safety_margin = max(256, context_window // 16)
    else:
        reserved_output = 8192 if context_window > DEFAULT_CONTEXT_WINDOW else 4096
        reserved_system = 4096
        safety_margin = max(2048, context_window // 64)
    if context_window >= MAX_CONTEXT_WINDOW:
        mode = "256k-stress"
    elif context_window >= 196_608:
        mode = "192k"
    elif context_window >= DEFAULT_CONTEXT_WINDOW:
        mode = "128k-balanced"
    else:
        mode = f"{max(1, context_window // 1024)}k"
    return {
        "active_model": model or cfg.model,
        "model_alias": cfg.model,
        "main_endpoint": cfg.base_url,
        "ctx_size": context_window,
        "mode": mode,
        "reserved_output_tokens": reserved_output,
        "reserved_system_tokens": reserved_system,
        "safety_margin_tokens": safety_margin,
        "safe_input_budget": max(
            1024,
            context_window - reserved_output - reserved_system - safety_margin,
        ),
        "timeout_policy": {
            "chat": TIMEOUT_POLICY_SECONDS["chat"],
            "code": TIMEOUT_POLICY_SECONDS["code"],
            "long_context": (
                TIMEOUT_POLICY_SECONDS["long_context_256k"]
                if context_window >= MAX_CONTEXT_WINDOW
                else TIMEOUT_POLICY_SECONDS["long_context_128k"]
            ),
        },
        "source": source,
    }
