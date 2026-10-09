"""Run-scoped built-in capability groups selected by the model.

This is prompt composition, not authorization: every tool remains implemented
and executable through the canonical provider/executor path once its schema has
been requested for the current run.
"""
from __future__ import annotations

from collections.abc import Collection
import re

CORE_BUILTIN_TOOL_ORDER: tuple[str, ...] = (
    "capability_load",
)

CORE_BUILTIN_TOOLS = frozenset(CORE_BUILTIN_TOOL_ORDER)


CAPABILITY_GROUPS: dict[str, frozenset[str]] = {
    "project": frozenset({
        "read_file", "write_file", "edit_file", "glob", "grep",
        "project_map", "recall", "todo_update", "delegate_task", "run_bash", "run_server",
    }),
    "mcp": frozenset({"mcp"}),
    # ssh/itops also switch on their integration provider (PROVIDER_GROUPS).
    "resources": frozenset({
        "resource_process", "resource_materialize", "resource_publish",
    }),
    "memory": frozenset({"memory", "library"}),
    "math": frozenset({"calc", "unit_convert"}),
}


CAPABILITY_GROUP_DESCRIPTIONS: dict[str, str] = {
    "project": "project overview (project_map), project index search (recall) and a sub-agent (delegate_task); files and shell are always loaded",
    "mcp": "MCP servers (each has an <id>-mcp skill): list, start/stop, their tools, add or change a server",
    "resources": "attachment metadata, workspace copies and verified downloads",
    "memory": "long-term facts about the user (memory) and the user's document Library (library)",
    "math": "exact arithmetic, calendar and unit conversion; domain calculations use skills",
}


ALL_BUILTIN_TOOLS = frozenset().union(
    CORE_BUILTIN_TOOLS,
    *CAPABILITY_GROUPS.values(),
)


_DOWNLOAD_REQUEST_RE = re.compile(
    r"(?:скач\w*|download|дай\s+(?:мне\s+)?файл|"
    r"отдай\s+(?:мне\s+)?файл|файл\w*\s+для\s+скач\w*)",
    re.IGNORECASE,
)
_LINK_REQUEST_RE = re.compile(r"(?:ссылк\w*|\blink\b)", re.IGNORECASE)
_FILE_LINK_REQUEST_RE = re.compile(
    r"(?:ссылк\w*\s+на\s+(?:этот\s+)?файл\b|"
    r"\blink\s+to\s+(?:the\s+)?file\b)",
    re.IGNORECASE,
)
_GENERATED_ARTIFACT_RE = re.compile(
    r"\b(?:создай|создан\w*|сгенер\w*|сформир\w*|подготов\w*|готов\w*|"
    r"create|creat(?:ed|ing)|generat(?:e|ed|ing)|prepare[ds]?)\s+"
    r"[^.!?\n]{0,80}\b(?:pdf|docx|xlsx|pptx|csv|zip|файл\w*|документ\w*|"
    r"отч[её]т\w*|таблиц\w*|презентац\w*|архив\w*|file|document|report)\b",
    re.IGNORECASE,
)
# Groups whose tools come from an integration provider, by tool-name prefix.
PROVIDER_GROUPS: dict[str, str] = {}


def file_delivery_requested(user_message: str) -> bool:
    """The user asked for a downloadable file: a guidance hint, never a completion gate.

    A product/source link needs web evidence, not a locally published file; these
    words can also describe software to build.
    """
    text = str(user_message or "")
    return bool(
        _DOWNLOAD_REQUEST_RE.search(text)
        or _FILE_LINK_REQUEST_RE.search(text)
        or (_LINK_REQUEST_RE.search(text) and _GENERATED_ARTIFACT_RE.search(text))
    )






def normalize_capability_groups(groups: Collection[str] | None) -> frozenset[str]:
    """Return known normalized group names; stale journal values are ignored."""
    return frozenset(
        name
        for value in groups or ()
        if (name := str(value).strip().lower()) in CAPABILITY_GROUPS
    )


def builtin_tools_for_groups(groups: Collection[str] | None) -> frozenset[str]:
    """Core tool names plus every tool in the selected capability groups."""
    selected = set(CORE_BUILTIN_TOOLS)
    for group in normalize_capability_groups(groups):
        selected.update(CAPABILITY_GROUPS[group])
    return frozenset(selected)


def capability_catalog_text() -> str:
    """Compact model-facing catalog; exact schemas arrive only after loading."""
    return "\n".join(
        f"- {name}: {CAPABILITY_GROUP_DESCRIPTIONS[name]}"
        for name in CAPABILITY_GROUPS
    )
