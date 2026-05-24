"""Compatibility facade for chat identity guard policy."""

from __future__ import annotations

from app.application.chat.identity_guard import (
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

__all__ = [
    "FIRST_PERSON_RE",
    "IDENTITY_QUESTION_RE",
    "MODEL_IDENTITY_RE",
    "SENTENCE_SPLIT_RE",
    "_contains_model_identity",
    "_rewrite_identity_drift",
    "_safe_identity_reply",
    "_still_drifting",
    "guard_identity_response",
    "is_identity_question",
]
