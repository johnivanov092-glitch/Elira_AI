"""Thin facade — all identity guard logic lives in application/policy/identity_guard.py."""
from app.application.policy.identity_guard import (  # noqa: F401
    FIRST_PERSON_RE,
    IDENTITY_QUESTION_RE,
    MODEL_IDENTITY_RE,
    SENTENCE_SPLIT_RE,
    _contains_model_identity,
    _rewrite_identity_drift,
    _safe_identity_reply,
    _still_drifting,
    guard_identity_response,
    is_identity_question,
)
