"""Durable media resources (R1) — upload ≠ processing.

An uploaded file is stored as a durable RAW resource first; nothing is
extracted, transcribed, OCR'd, or sent anywhere at upload time. After a user
prompt, the model explicitly processes it with ``resource_process`` or makes a
workspace copy with ``resource_materialize`` for a skill. Durable resource ids
remain usable across Workflow runs.

Layout:
- ``resource_store``  — durable byte store + metadata sidecars under the data
  root; streaming intake with temp-file + atomic rename, and
  containment (the user filename is never part of a trusted path);
- ``processing``      — explicit local-CPU metadata inspection only; document/OCR/audio
  processing belongs to mutable skills.

The model boundary is ``resource_store.resource_ref`` — {resource_id, name,
kind, content_type, size}. Absolute/storage paths and raw bytes never cross it.
"""
from __future__ import annotations

from app.application.media.resource_store import (  # noqa: F401
    ResourceError,
    ResourceRecord,
    ResourceTooLarge,
    classify_kind,
    get_record,
    read_bytes,
    register_resource,
    resource_ref,
)

__all__ = [
    "ResourceError",
    "ResourceRecord",
    "ResourceTooLarge",
    "classify_kind",
    "get_record",
    "read_bytes",
    "register_resource",
    "resource_ref",
]
