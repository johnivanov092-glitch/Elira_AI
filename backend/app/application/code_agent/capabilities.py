"""Run-scoped built-in capability groups selected by the model.

This is prompt composition, not authorization: every tool remains implemented
and executable through the canonical provider/executor path once its schema has
been requested for the current run.
"""
from __future__ import annotations

from collections.abc import Collection


CORE_BUILTIN_TOOLS = frozenset({
    "capability_load",
    "runtime_control",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "path_exists",
    "project_map",
    "todo_update",
    "delegate_task",
    "run_bash",
})


CAPABILITY_GROUPS: dict[str, frozenset[str]] = {
    "web": frozenset({
        "web_search", "web_fetch", "web_query", "web_sitemap", "http_api",
        "browser", "screenshot",
    }),
    "desktop": frozenset({"computer"}),
    "resources": frozenset({
        "resource_process", "resource_remote_process", "resource_materialize",
        "resource_publish", "read_image", "ocr_file", "file_gen",
    }),
    "data": frozenset({
        "sandbox_run", "sandbox_reset", "translator", "regex", "csv",
        "converter", "sql", "encrypt", "archiver",
    }),
    "memory": frozenset({"recall", "remember"}),
    "operations": frozenset({"run_server", "reconcile_server_facts", "webhook"}),
}


CAPABILITY_GROUP_DESCRIPTIONS: dict[str, str] = {
    "web": "internet search, page reading, HTTP APIs, JS browser and URL screenshots",
    "desktop": "local Windows desktop screenshots, mouse and keyboard control",
    "resources": "attachments, OCR/vision, generated DOCX/XLSX/PDF and downloads",
    "data": "sandboxed code, regex, CSV, conversion, SQLite, encryption and archives",
    "memory": "semantic recall and durable user facts/corrections",
    "operations": "long-lived dev servers, inference-server facts and webhooks",
}


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
