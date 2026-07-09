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
    is NEVER trusted at render (W3 review). W5 adds, per claim: CORROBORATION —
    how many INDEPENDENT registrable domains back the VERIFIED evidence (single vs
    ≥2) — and FRESHNESS of each source (its date; stale sources flagged). Conflicts
    the model marked (advisory) get a dedicated section. quote_verified is
    provenance only, never proof the claim is true; support_note is advisory."""
    from app.application.web_evidence.freshness import (
        corroboration, freshness_note, registrable_domain)

    claims = _store.list_claims(run_id)
    if not claims:
        return ""
    try:
        docs = {d["doc_id"]: d for d in _store.list_documents(run_id)}
    except Exception:
        docs = {}

    lines = ["## Реестр цитат (перепроверено runtime сейчас — провенанс, не истинность утверждения)"]
    n_unverified = 0
    single_source = 0
    conflicted_idx: list[int] = []
    for i, c in enumerate(claims, 1):
        lines.append(f"\n**[{i}] {c['claim_text']}**")
        if c["support_note"]:
            lines.append(f"  _модель (advisory): {c['support_note']}_")
        if c["conflicted"]:
            conflicted_idx.append(i)
        verified_domains: set[str] = set()
        verified_tiers: set[str] = set()
        any_now = False
        for e in c["evidence"]:
            doc_id = e.get("doc_id")
            fresh = verify_quote(run_id, str(doc_id), e["quote"], offset=e.get("offset")) if doc_id \
                else {"quote_verified": False, "source_verified": False,
                      "reason": "нет doc_id — цитату нельзя привязать к источнику"}
            d = docs.get(doc_id or "", {})
            url = d.get("final_url") or d.get("url")
            if fresh["quote_verified"]:
                any_now = True
                if url:
                    verified_domains.add(registrable_domain(url))
                    verified_tiers.add(str(d.get("tier") or "unknown"))
            src = registrable_domain(url) if url else (doc_id or "—")
            quote = " ".join((e["quote"] or "").split())[:200]
            fnote = f" · {freshness_note(d.get('dates') or {})}" if url else ""
            # W6 tier annotation — publisher KIND (deterministic list), not a verdict
            tier = str(d.get("tier") or "unknown")
            tnote = f" · {tier}" if url and tier != "unknown" else ""
            lines.append(f"  • {src}: «{quote}» — {_badge(fresh)}{fnote}{tnote}")
        level, cnote = corroboration(verified_domains)
        # invariant №9: an OFFICIAL primary source may suffice alone — annotate,
        # but keep the single-source count honest (annotation, not exemption).
        if level == "single" and verified_tiers == {"official"}:
            cnote += " · источник official (вендор/стандарт) — может быть достаточен один"
        lines.append(f"  → {cnote}")
        if not any_now:
            n_unverified += 1
        if level == "single":
            single_source += 1

    if conflicted_idx:
        lines.append("\n### ⚠ Противоречия (отмечено моделью — advisory, не runtime-вердикт)")
        lines.append("  Утверждения с конфликтующими источниками: "
                     + ", ".join(f"[{i}]" for i in conflicted_idx))

    foot = [f"\nВсего утверждений с evidence: {len(claims)}"]
    if n_unverified:
        foot.append(f"без подтверждённого провенанса СЕЙЧАС: {n_unverified}")
    if single_source:
        foot.append(f"одноисточниковых (не перекрёстно): {single_source}")
    lines.append("; ".join(foot))
    return "\n".join(lines)
