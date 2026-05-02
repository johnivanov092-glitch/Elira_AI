"""Compatibility route aliases for the legacy phase20 queue module name."""
from __future__ import annotations

from app.api.routes.elira_preview_queue import PreviewQueuePayload, preview_queue, router

__all__ = ["PreviewQueuePayload", "preview_queue", "router"]
