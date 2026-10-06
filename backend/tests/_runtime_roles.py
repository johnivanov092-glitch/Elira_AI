"""Test helpers for the provider role projection (owner's decision 2026-10-06).

Runtime blocks reach the model inside one section at the end of the leading
system message; notices the model must act on now are appended to the latest
tool result. The user role carries only the owner's text.
"""
from __future__ import annotations

from typing import Any

from app.application.code_agent.history import _RUNTIME_NOTICE_HEADER, RUNTIME_SECTION_HEADER

RESTORED_SOURCES_HEADER = "[СОХРАНЁННЫЕ ВЕБ-ВЫДЕРЖКИ"


def _system(messages: list[dict[str, Any]]) -> str:
    if messages and messages[0].get("role") == "system":
        return str(messages[0].get("content") or "")
    return ""


def base_system(messages: list[dict[str, Any]]) -> str:
    """The stable system prompt before the runtime section."""
    return _system(messages).split("\n\n" + RUNTIME_SECTION_HEADER, 1)[0]


def runtime_section(messages: list[dict[str, Any]]) -> str:
    """Runtime blocks in the system section (empty when there are none)."""
    system = _system(messages)
    index = system.find(RUNTIME_SECTION_HEADER)
    return system[index + len(RUNTIME_SECTION_HEADER):] if index >= 0 else ""


def runtime_notices(messages: list[dict[str, Any]]) -> str:
    """Act-now notices appended to tool results."""
    parts = []
    for message in messages:
        content = str(message.get("content") or "")
        if message.get("role") == "tool" and _RUNTIME_NOTICE_HEADER in content:
            parts.append(content.split(_RUNTIME_NOTICE_HEADER, 1)[1])
    return "\n".join(parts)


def runtime_text(messages: list[dict[str, Any]]) -> str:
    """Everything the runtime told the model in this request."""
    return runtime_section(messages) + "\n" + runtime_notices(messages)


def restored_sources(messages: list[dict[str, Any]]) -> str:
    """The restored web-excerpt block: in the tool channel, or (before the
    slice's first tool result) in the runtime section."""
    for message in reversed(messages):
        content = str(message.get("content") or "")
        if message.get("role") == "tool" and RESTORED_SOURCES_HEADER in content:
            block = content[content.find(RESTORED_SOURCES_HEADER):]
            return block.split("\n\n" + _RUNTIME_NOTICE_HEADER, 1)[0]
    section = runtime_section(messages)
    index = section.find(RESTORED_SOURCES_HEADER)
    return section[index:] if index >= 0 else ""


def original_tool_text(messages: list[dict[str, Any]]) -> str:
    """Tool results without restored excerpts or runtime notices appended to them."""
    texts = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = str(message.get("content") or "")
        for marker in ("\n\n" + RESTORED_SOURCES_HEADER, "\n\n" + _RUNTIME_NOTICE_HEADER):
            content = content.split(marker, 1)[0]
        texts.append(content)
    return "\n".join(texts)


def user_texts(messages: list[dict[str, Any]]) -> list[str]:
    return [str(message.get("content") or "") for message in messages if message.get("role") == "user"]
