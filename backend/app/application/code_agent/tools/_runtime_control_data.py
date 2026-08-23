"""Memory and library runtime_control operations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.code_agent.tools._runtime_control_contract import (
    input_request,
    require_id,
)


def memory_control(
    operation: str,
    memory_id: int | str | None,
    query: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    from app.application.memory import facade

    profile = str(config.get("profile") or "").strip() or None
    if operation == "memory_stats":
        return facade.fact_stats(profile=profile)
    if operation == "memory_profiles":
        return facade.list_profiles()
    if operation == "memory_list":
        return facade.list_facts(
            limit=max(1, int(config.get("limit") or 50)),
            profile=profile,
            category=str(config.get("category") or "").strip() or None,
        )
    if operation == "memory_search":
        search_query = require_id(
            query or str(config.get("query") or ""),
            "query",
            "поисковый запрос",
        )
        return facade.search_facts(
            search_query,
            limit=max(1, int(config.get("limit") or 10)),
            profile=profile,
        )
    if operation == "memory_recall":
        recall_query = require_id(
            query or str(config.get("query") or ""),
            "query",
            "запрос памяти",
        )
        return facade.recall(
            recall_query,
            profile=profile,
            project=str(config.get("project") or "").strip() or None,
            fact_limit=max(0, int(config.get("fact_limit") or 5)),
            semantic_limit=max(0, int(config.get("semantic_limit") or 3)),
            max_chars=max(1, int(config.get("max_chars") or 2000)),
        )
    if operation == "memory_add":
        value = require_id(
            query or str(config.get("text") or ""),
            "query",
            "текст факта",
        )
        return facade.add_fact(
            value,
            category=str(config.get("category") or "fact"),
            source=str(config.get("source") or "runtime_control"),
            importance=int(config.get("importance") or 5),
            profile=profile,
        )
    if operation == "memory_delete":
        mid = memory_id if memory_id not in (None, "") else config.get("memory_id")
        if mid in (None, ""):
            raise input_request(
                "Укажите запись памяти для удаления.",
                "memory_id",
                "ID записи памяти",
            )
        return facade.delete_fact(mid, profile=profile)
    if operation == "memory_prune":
        return facade.prune(
            max_age_days=max(1, int(config.get("max_age_days") or 30)),
            max_importance=int(config.get("max_importance") or 3),
            dry_run=bool(config.get("dry_run", False)),
        )
    raise ValueError(f"unsupported memory operation: {operation}")


def library_control(
    operation: str,
    project_root: Path,
    path: str,
    filename: str,
    query: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    from app.application.library import runtime

    if operation == "library_list":
        return runtime.list_library_files()
    if operation == "library_search":
        return runtime.search_files(query or str(config.get("query") or ""))
    if operation == "library_context":
        return runtime.build_library_context(
            max_files=max(1, int(config.get("max_files") or 10)),
            max_chars_per_file=max(1, int(config.get("max_chars_per_file") or 2500)),
        )
    if operation == "library_add":
        raw_path = require_id(
            path or str(config.get("path") or ""),
            "path",
            "путь к файлу",
        )
        source_path = Path(raw_path).expanduser()
        if not source_path.is_absolute():
            source_path = project_root / source_path
        source_path = source_path.resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Файл не найден: {source_path}")
        return runtime.add_file_contents(
            filename=filename or str(config.get("filename") or source_path.name),
            contents=source_path.read_bytes(),
            content_type=str(config.get("content_type") or "") or None,
            use_in_context=bool(config.get("active", True)),
            source="runtime_control",
        )
    if operation == "library_toggle":
        file_id = config.get("file_id")
        active = bool(config.get("active", True))
        if file_id not in (None, ""):
            return runtime.toggle_context(int(file_id), enabled=active)
        selected = require_id(
            filename or str(config.get("filename") or ""),
            "filename",
            "имя файла",
        )
        return runtime.set_library_active(selected, active)
    if operation == "library_delete":
        file_id = config.get("file_id")
        if file_id not in (None, ""):
            return runtime.delete_file(int(file_id))
        selected = require_id(
            filename or str(config.get("filename") or ""),
            "filename",
            "имя файла",
        )
        return runtime.delete_library_file(selected)
    raise ValueError(f"unsupported library operation: {operation}")
