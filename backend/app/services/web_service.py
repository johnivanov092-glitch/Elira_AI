"""Web service — compatibility shim.

All logic lives in ``app.application.web_service.runtime``.
Public API re-exported for all callers: application/tool_registry/builtins.
"""
from __future__ import annotations

from app.application.web_service.runtime import research_web, search_web

__all__ = ["research_web", "search_web"]
