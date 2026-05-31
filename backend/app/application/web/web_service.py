"""Compatibility facade for web search helpers."""
from __future__ import annotations

from app.infrastructure.search.web_search import research_web, search_web

__all__ = ["search_web", "research_web"]
