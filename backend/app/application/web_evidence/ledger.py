"""W3 citation ledger — structured claims with deterministically verified evidence.

The ONLY way a claim enters the ledger is the structured web_claim_add tool call
(contract §6): numbers [n] in the model's prose are NEVER parsed. For each piece
of evidence the runtime computes, deterministically, quote_verified (the quote
appears verbatim in the stored canonical_text, hash intact) and source_verified
(the document was really fetched by this run). support_note is the model's
ADVISORY assessment that the quote supports the claim — the runtime NEVER asserts
that (negative rule №1: quote_verified is provenance, not truth of the claim).

render_ledger() produces the deterministic appendix the runtime appends to the
final answer — the model does not format citations.
"""
from __future__ import annotations

from app.application.web_evidence.retrieval import verify_quote
from app.infrastructure.web_corpus import store as _store

# Bounds (contract §6) — a batched call, but bounded so it can't bloat the run.
MAX_CLAIMS_PER_CALL = 10
MAX_EVIDENCE_PER_CLAIM = 4
MAX_QUOTE_CHARS = 500


class LedgerBoundsError(Exception):
    """A web_claim_add payload exceeds the structural bounds — honest ok=False."""


def add_claims(run_id: str, claims: list[dict]) -> dict:
    """Validate + record a batch of claims. Each claim: {claim, evidence:[{doc_id,
    chunk_id?, quote}], support?, conflicted?}. Returns per-claim verdicts. Raises
    LedgerBoundsError on a payload that breaks the bounds (nothing is stored)."""
    if not isinstance(claims, list) or not claims:
        raise LedgerBoundsError("claims must be a non-empty list")
    if len(claims) > MAX_CLAIMS_PER_CALL:
        raise LedgerBoundsError(f"максимум {MAX_CLAIMS_PER_CALL} claims за вызов, получено {len(claims)}")
    # Validate the WHOLE batch before storing anything (all-or-nothing per call).
    for c in claims:
        ev = c.get("evidence") or []
        if not isinstance(c.get("claim"), str) or not c["claim"].strip():
            raise LedgerBoundsError("каждый claim должен иметь непустой текст 'claim'")
        if not isinstance(ev, list) or not ev:
            raise LedgerBoundsError("каждый claim должен иметь хотя бы один evidence")
        if len(ev) > MAX_EVIDENCE_PER_CLAIM:
            raise LedgerBoundsError(f"максимум {MAX_EVIDENCE_PER_CLAIM} evidence на claim")
        for e in ev:
            if not isinstance(e.get("quote"), str) or not e["quote"].strip():
                raise LedgerBoundsError("каждый evidence должен иметь непустой 'quote'")
            if len(e["quote"]) > MAX_QUOTE_CHARS:
                raise LedgerBoundsError(f"quote длиннее {MAX_QUOTE_CHARS} символов")

    results = []
    for c in claims:
        verified_ev = []
        for e in c["evidence"]:
            doc_id = str(e.get("doc_id") or "").strip()
            v = verify_quote(run_id, doc_id, e["quote"],
                             offset=e.get("offset")) if doc_id else {
                "quote_verified": False, "source_verified": False,
                "reason": "нет doc_id — цитату нельзя привязать к источнику"}
            verified_ev.append({
                "doc_id": doc_id or None, "chunk_id": e.get("chunk_id"),
                "quote": e["quote"], "offset": v.get("offset"),
                "quote_verified": bool(v["quote_verified"]),
                "source_verified": bool(v["source_verified"]),
                "reason": v.get("reason"),
            })
        claim_id = _store.add_claim(
            run_id=run_id, claim_text=c["claim"].strip(),
            support_note=(str(c["support"]).strip()[:400] if c.get("support") else None),
            conflicted=bool(c.get("conflicted")), evidence=verified_ev)
        results.append({
            "claim_id": claim_id, "claim": c["claim"].strip(),
            "evidence": [{"doc_id": e["doc_id"], "quote_verified": e["quote_verified"],
                          "source_verified": e["source_verified"], "reason": e["reason"]}
                         for e in verified_ev],
            "any_verified": any(e["quote_verified"] for e in verified_ev),
        })
    return {"ok": True, "recorded": len(results), "claims": results}


def _badge(ev: dict) -> str:
    if ev["quote_verified"]:
        return "провенанс ✓ (цитата дословно в источнике)"
    if ev["source_verified"]:
        return "источник ✓, но цитата НЕ найдена дословно ✗"
    return "НЕ подтверждено ✗ (нет в корпусе этого рана)"


def render_ledger(run_id: str) -> str:
    """Deterministic citation appendix the runtime appends to the final answer.

    Provenance is a property of the CURRENT corpus, not of record time: every
    evidence item is RE-VERIFIED against the source here (verify_quote — quote
    verbatim + hash intact + document still present), so a stored `quote_verified`
    is NEVER trusted at render (John's W3 review). A source tampered or TTL-expired
    after web_claim_add therefore renders as unverified, not a stale ✓.
    quote_verified is provenance only — never proof the claim is true; support_note
    is the model's advisory opinion."""
    claims = _store.list_claims(run_id)
    if not claims:
        return ""
    lines = ["## Реестр цитат (перепроверено runtime сейчас — провенанс, не истинность утверждения)"]
    n_unverified = 0
    for i, c in enumerate(claims, 1):
        flag = " ⚠ конфликт источников" if c["conflicted"] else ""
        lines.append(f"\n**[{i}] {c['claim_text']}**{flag}")
        if c["support_note"]:
            lines.append(f"  _модель (advisory): {c['support_note']}_")
        any_now = False
        for e in c["evidence"]:
            doc_id = e.get("doc_id")
            # RE-VERIFY against the current corpus — do not trust the stored verdict.
            fresh = verify_quote(run_id, str(doc_id), e["quote"], offset=e.get("offset")) if doc_id \
                else {"quote_verified": False, "source_verified": False,
                      "reason": "нет doc_id — цитату нельзя привязать к источнику"}
            any_now = any_now or bool(fresh["quote_verified"])
            src = doc_id or "—"
            quote = " ".join((e["quote"] or "").split())[:200]
            lines.append(f"  • {src}: «{quote}» — {_badge(fresh)}")
        if not any_now:
            n_unverified += 1
    foot = f"\nВсего утверждений с evidence: {len(claims)}"
    if n_unverified:
        foot += f"; из них БЕЗ подтверждённого провенанса СЕЙЧАС: {n_unverified}"
    lines.append(foot)
    return "\n".join(lines)
