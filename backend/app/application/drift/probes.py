"""Live-fact probes for the drift detector.

Ground truth comes from the running llama-server's ``/props`` over HTTP (LAN) —
it exposes ``model_path`` and ``n_ctx`` directly, which is exactly the set of
facts that stale docs got wrong (active model, context window). No ssh, no
credentials, no ``docker inspect``. A fact is ``None`` when the server is
unreachable/unparseable so the caller can tell "unknown" from "changed".
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from app.infrastructure.llm.openai_compatible import local_llm_config

logger = logging.getLogger(__name__)

_PROPS_TIMEOUT_S = 8.0

# Human labels for facts, used in alerts / status.
FACT_LABELS = {
    "active_model": "Active model file",
    "server_context_window": "Server context window (n_ctx)",
}


def _props_root(base_url: str) -> str:
    # base_url ends with /v1 (OpenAI-compat); /props lives at the server root.
    return base_url[:-3].rstrip("/") if base_url.endswith("/v1") else base_url.rstrip("/")


def fetch_server_props() -> dict[str, Any] | None:
    """Raw ``/props`` dict from the active llama-server, or None if unreachable.

    Deliberately uncached (unlike ``openai_compatible.server_context_window``):
    drift detection must see a change the moment it happens. We do NOT gate on
    ``cfg.enabled`` — that flag means "does the app route inference here", not
    "is the server alive"; the drift detector probes the physical server either
    way. The fetch fails gracefully to None on any error.
    """
    cfg = local_llm_config()
    base_url = (getattr(cfg, "base_url", "") or "").strip()
    if not base_url:
        return None
    timeout = getattr(cfg, "timeout_seconds", _PROPS_TIMEOUT_S) or _PROPS_TIMEOUT_S
    try:
        root = _props_root(base_url)
        resp = requests.get(f"{root}/props", timeout=min(_PROPS_TIMEOUT_S, timeout))
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:  # unreachable / bad JSON / timeout — treat as unknown
        logger.info("drift: /props probe failed: %s", exc)
        return None


def probe_live_facts() -> dict[str, str | None]:
    """Ground-truth values for the registered live facts. Missing → None."""
    facts: dict[str, str | None] = {"active_model": None, "server_context_window": None}
    data = fetch_server_props()
    if not data:
        return facts

    model_path = data.get("model_path")
    if isinstance(model_path, str) and model_path.strip():
        facts["active_model"] = model_path.strip()

    gen = data.get("default_generation_settings")
    n_ctx = gen.get("n_ctx") if isinstance(gen, dict) else None
    if n_ctx is None:
        n_ctx = data.get("n_ctx")
    if isinstance(n_ctx, int) and n_ctx > 0:
        facts["server_context_window"] = str(n_ctx)

    return facts
