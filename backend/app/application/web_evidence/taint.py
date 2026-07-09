"""Intent-binding for the web-research contour (contract §3, John's P1-1).

Bypass is the everyday working mode, so the permission gate alone is NOT an
injection mitigation: a malicious page could talk the model into a side-effect
call that bypass would silently auto-approve. This module provides the
deterministic CORPUS-TAINT check: a side-effect tool call whose string
arguments contain a verbatim fragment (≥ _WINDOW chars, normalized) of the
run's web corpus that is ABSENT from the user's own task text is escalated to
the critical tier — explicit human approval in EVERY mode, bypass included.

Failure direction: escalate to approval, never silent-block and never
auto-pass. Guard-class heuristic (logic-map invariant №10): a false positive
costs one extra confirmation; false negatives are narrowed by the data
envelope + the adversarial smoke set, honestly not zero.

Fail-open on store failure is SAFE here: if the corpus store is down, corpus
text could not have entered the model's context this run either (web_query
degrades too), so there is nothing to be tainted by.
"""
from __future__ import annotations

import logging

from app.application.web_evidence.analyzer import normalize

logger = logging.getLogger(__name__)

_WINDOW = 24     # min verbatim overlap that counts as corpus-derived
_STRIDE = 12
_MAX_ARG_CHARS = 20_000   # bound the scan; longer args are truncated for the check

# Tools whose arguments can cause side effects worth binding to user intent.
SIDE_EFFECT_TOOLS = frozenset({
    "run_bash", "run_server", "write_file", "edit_file", "sandbox_run",
    "ssh_run", "ssh_run_ps", "ssh_write", "ssh_replace",
    "computer", "webhook", "sql", "file_gen", "archiver", "encrypt", "delegate_task",
})


def _string_values(args: dict) -> list[str]:
    out: list[str] = []
    def walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(args or {})
    return out


def corpus_tainted(run_id: str, tool_name: str, args: dict, user_text: str) -> str | None:
    """The matched corpus fragment when `args` of a side-effect call carry
    verbatim web-corpus content that the USER never wrote — else None."""
    if tool_name not in SIDE_EFFECT_TOOLS:
        return None
    try:
        from app.infrastructure.web_corpus import store
        if not store.has_documents(run_id):
            return None
        corpus = "\n".join(normalize(t) for t in store.corpus_texts(run_id))
    except Exception as exc:  # noqa: BLE001 — see module docstring (safe fail-open)
        logger.warning("corpus taint check unavailable (%s) — skipped", exc)
        return None
    if not corpus:
        return None
    user_norm = normalize(user_text or "")
    for raw in _string_values(args):
        arg = normalize(raw)[:_MAX_ARG_CHARS]
        if len(arg) < _WINDOW:
            continue
        for start in range(0, len(arg) - _WINDOW + 1, _STRIDE):
            frag = arg[start:start + _WINDOW]
            if frag in user_norm:
                continue        # the user themselves wrote it — intent-bound
            if frag in corpus:
                return frag     # corpus-derived, not user-derived → escalate
    return None
