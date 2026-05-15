"""Thin facade — all provenance guard logic lives in application/policy/provenance_guard.py."""
from app.application.policy.provenance_guard import (  # noqa: F401
    _normalize_whitespace,
    _rewrite_direct_personal_facts,
    _rewrite_natural_provenance,
    _strip_raw_markers,
    _strip_technical_source_phrases,
    guard_provenance_response,
    is_provenance_question,
)
