"""Application adapter to the mutable web-research answer link checks; no domain implementation."""
from __future__ import annotations

from collections.abc import Collection
from typing import Any

from app.core.skill_modules import load_skill_module

_MODULE = "webskill/application/code_agent/answer_contracts.py"


def unread_link_violations(answer_text: str, *, read_source_urls: Collection[str],
                           known_source_urls: Collection[str]) -> tuple[Any, ...]:
    module = load_skill_module("web-research", _MODULE)
    return module.web_source_citation_violations(
        answer_text, read_source_urls=read_source_urls, known_source_urls=known_source_urls)


def mark_unread_links(answer_text: str, violations: Collection[Any], *, failed_errors: dict[str, str]) -> str:
    module = load_skill_module("web-research", _MODULE)
    return module.mark_unread_web_links(answer_text, violations, failed_errors=failed_errors)


def render_numbered_links(answer_text: str, pages: list[tuple[str, str]]) -> str:
    module = load_skill_module("web-research", _MODULE)
    return module.render_numbered_citations(answer_text, pages)
