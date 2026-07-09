"""R1: the Verifier Coverage Catalog as a runtime ASSIST source.

Gated by the `catalog_assist` feature flag (default OFF — behavior is bit-identical
when off). When ON, the runtime reads verifier_catalog.yaml for three things:

  * an intent SUPPORT index — is there a verifier that can close this intent?
    Criteria without one get an honest «нет верификатора» label in the readiness
    panel instead of a silent `unconfirmed`;
  * per-intent `does_not_close` notes — appended to closure hints (single source
    instead of hand-maintained warning text);
  * a DRIFT check — an intent the classifier emits that the catalog doesn't know
    is logged: the catalog and the code have drifted apart.

Deliberately NOT a classifier: criterion→intent classification stays in code
(taskspec._criterion_intent). The catalog's criterion_patterns are illustrative
examples, not matchable patterns — replacing classification wholesale is exactly
the blast radius this flag-gated assist avoids (AGENT_LOGIC_MAP §5 R1).

Fail-open everywhere: a missing/корявый yaml or absent PyYAML → every helper
degrades to "no opinion" and the runtime behaves exactly as with the flag off.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).with_name("verifier_catalog.yaml")
_LOCK = threading.Lock()
_UNREAD = object()   # cache sentinel: "never attempted for this mtime"
_CACHE: dict = {"mtime": _UNREAD, "index": None, "known": frozenset()}

# Intents that are unverifiable BY DESIGN — the floor holds even if the catalog
# file is unreadable (fail-open must not un-label the honest generic class).
_NEVER_VERIFIABLE = frozenset({"generic", "report"})

# Full-token intent name: 'command_output (output_absent)' → first token matches;
# a row-id cross-reference like 'cleanup.confirmed' does NOT (no phantom intents
# defeating the drift canary — review F8).
_INTENT_TOKEN_RE = re.compile(r"^[a-z_]+$")
_RANK = {"supported": 2, "partial": 1, "missing": 0}


def _base_intents(value) -> set[str]:
    """Base intent names in a row field — the FIRST whitespace token, and only when
    it is a bare [a-z_] identifier; qualifiers after it are ignored."""
    out: set[str] = set()
    for part in [value] if isinstance(value, str) else list(value or []):
        toks = str(part).strip().split()
        if toks and _INTENT_TOKEN_RE.fullmatch(toks[0]):
            out.add(toks[0])
    return out


def _build() -> tuple[dict | None, frozenset]:
    """Parse the catalog into (index, known). The ENTIRE parse is fail-open (review
    F1: a malformed-but-parseable document — top-level list, string rows, blank
    support — must degrade to no-op, never crash a run)."""
    try:
        import yaml
        raw = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"catalog root must be a mapping, got {type(raw).__name__}")
        index: dict[str, dict] = {}
        for row in raw.get("categories") or []:
            if not isinstance(row, dict):
                continue
            support_toks = str(row.get("support") or "missing").lower().split()
            support = support_toks[0] if support_toks else "missing"
            # An unrecognized support value ranks as PARTIAL, not missing — a typo must
            # not mislabel verifiable criteria «нет верификатора» (review F5); the
            # catalog validation test enforces the valid value set in-repo anyway.
            rank = _RANK.get(support, _RANK["partial"])
            notes = [str(n) for n in (row.get("does_not_close") or [])
                     if str(n).strip() and " " in str(n).strip()]
            # prose only: bare intent-token cross-references are machine metadata,
            # not text a nudge should carry (review F2/F7)
            for intent in _base_intents(row.get("intent")) | _base_intents(row.get("closes")):
                cur = index.setdefault(intent, {"rank": -1, "notes": []})
                cur["rank"] = max(cur["rank"], rank)
                if rank >= 1 and notes:
                    cur["notes"].extend(n for n in notes if n not in cur["notes"])
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        known = frozenset(str(i) for i in (meta.get("intents") or [])) | set(index)
        return index, known
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        logger.warning("verifier catalog unavailable (%s) — catalog assist is a no-op", exc)
        return None, frozenset()


def _index() -> tuple[dict | None, frozenset]:
    """The cached (index, known-intents) pair, rebuilt only when the yaml's mtime
    moves — a FAILED build is cached too (one warning per mtime, not one per call;
    review F4/F11)."""
    try:
        mtime = _CATALOG_PATH.stat().st_mtime
    except OSError:
        mtime = None
    with _LOCK:
        if _CACHE.get("mtime", _UNREAD) != mtime:
            idx, known = _build()
            _CACHE.update(mtime=mtime, index=idx, known=known)
        return _CACHE["index"], _CACHE["known"]


def unsupported_intent(intent: str) -> bool:
    """The catalog has NO verifier for this intent (or it's unverifiable by design) —
    the criterion can never be auto-confirmed; the readiness panel says so honestly.
    Unknown-to-catalog intents are NOT flagged (that's drift, reported separately)."""
    if intent in _NEVER_VERIFIABLE:
        return True
    idx, _known = _index()
    if idx is None:
        return False   # fail-open: no catalog → no opinion
    info = idx.get(intent)
    return info is not None and info["rank"] <= _RANK["missing"]


def intent_note(intent: str, limit: int = 140) -> str:
    """The catalog's first does_not_close note for the intent ('' when none) — a
    single-sourced warning for closure hints."""
    idx, _known = _index()
    if not idx:
        return ""
    notes = (idx.get(intent) or {}).get("notes") or []
    return (notes[0][:limit].rstrip()) if notes else ""


def drift_intents(intents: set[str]) -> list[str]:
    """Intents the classifier emitted that the catalog doesn't know at all — the
    code and the contract have drifted; surfaced as a log warning per run."""
    idx, known = _index()
    if idx is None:
        return []
    return sorted(i for i in intents if i not in known and i not in _NEVER_VERIFIABLE)
