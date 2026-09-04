"""Memory and library runtime_control operations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.code_agent.tools._runtime_control_contract import (
    input_request,
    require_id,
)
from app.application.projects.scope import project_scope_id


def memory_control(
    operation: str,
    memory_id: int | str | None,
    query: str,
    config: dict[str, Any],
    project_root: Path,
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
        project = str(config.get("project") or "").strip()
        if project and not project.startswith("scope:"):
            project_path = Path(project).expanduser()
            if not project_path.is_absolute():
                project_path = project_root / project_path
            project = project_scope_id(project_path)
        elif not project:
            project = project_scope_id(project_root)
        return facade.recall(
            recall_query,
            profile=profile,
            project=project,
            fact_limit=max(0, int(config.get("fact_limit") or 5)),
            semantic_limit=max(0, int(config.get("semantic_limit") or 3)),
            max_chars=max(1, int(config.get("max_chars") or 2000)),
        )
    if operation == "memory_add":
        value = require_id(
            query or str(config.get("fact") or config.get("text") or ""),
            "query",
            "текст факта",
        )
        replacement = memory_id if memory_id not in (None, "") else config.get("replaces_id")
        return facade.add_fact(
            value,
            category=str(config.get("category") or "fact"),
            source=("user_correction" if replacement is not None else "user_command"),
            importance=int(config.get("importance") or 5),
            profile=profile,
            replaces_id=replacement,
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
        max_age_days = max(1, int(config.get("max_age_days") or 30))
        dry_run = bool(config.get("dry_run", False))
        semantic = facade.prune(
            max_age_days=max_age_days,
            max_importance=int(config.get("max_importance") or 3),
            dry_run=dry_run,
        )
        volatile = facade.prune_volatile_facts(
            max_age_days=max(1, int(config.get("volatile_max_age_days") or 7)),
            dry_run=dry_run,
            profile=profile,
        )
        return {
            "ok": bool(semantic.get("ok")) and bool(volatile.get("ok")),
            "dry_run": dry_run,
            "candidates": int(semantic.get("candidates") or 0) + int(volatile.get("candidates") or 0),
            "pruned": int(semantic.get("pruned") or 0) + int(volatile.get("pruned") or 0),
            "semantic": semantic,
            "volatile_facts": volatile,
        }
    raise ValueError(f"unsupported memory operation: {operation}")


def project_control(
    operation: str,
    project_root: Path,
    root_path: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Expose the existing Project Corpus owner through Workflow control."""
    from app.application.code_agent.indexing import index_project, project_corpus_status

    target = Path(root_path).expanduser() if root_path.strip() else project_root
    if not target.is_absolute():
        target = project_root / target
    target = target.resolve()

    if operation == "project_status":
        return project_corpus_status(target)
    if operation == "project_index":
        raw_patterns = config.get("patterns")
        if raw_patterns is not None and not isinstance(raw_patterns, list):
            raise ValueError("config.patterns must be an array")
        patterns = (
            [str(pattern).strip() for pattern in raw_patterns if str(pattern).strip()]
            if raw_patterns is not None
            else None
        )
        if raw_patterns is not None and not patterns:
            raise ValueError("config.patterns must contain at least one pattern")
        if "replace" in config and not isinstance(config["replace"], bool):
            raise ValueError("config.replace must be a boolean")
        return index_project(
            target,
            patterns=patterns,
            replace=config.get("replace", True),
        )
    raise ValueError(f"unsupported project operation: {operation}")


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
        return runtime.search_files(
            query or str(config.get("query") or ""),
            limit=max(1, int(config.get("limit") or 10)),
        )
    if operation == "library_read":
        file_id = config.get("file_id")
        if file_id in (None, ""):
            raise input_request(
                "Укажите файл Library для чтения.",
                "file_id",
                "ID файла Library",
            )
        return runtime.read_library_file(
            int(file_id),
            offset=max(0, int(config.get("offset") or 0)),
            limit=max(1, int(config.get("limit") or 8000)),
        )
    if operation == "library_context":
        return runtime.build_library_context(
            max_files=max(1, int(config.get("max_files") or 10)),
            max_chars_per_file=max(1, int(config.get("max_chars_per_file") or 2500)),
            query=query or str(config.get("query") or ""),
        )
    if operation == "library_add":
        configured_path = path or str(config.get("path") or "")
        filename_alias = filename or str(config.get("filename") or "")
        raw_path = require_id(
            configured_path or filename_alias,
            "path",
            "путь к файлу",
        )
        source_path = Path(raw_path).expanduser()
        if not source_path.is_absolute():
            source_path = project_root / source_path
        source_path = source_path.resolve()
        requested_filename = filename_alias if configured_path else ""
        if source_path.is_dir():
            named_candidate = (
                (source_path / requested_filename).resolve()
                if requested_filename
                else None
            )
            if named_candidate is not None and named_candidate.is_file():
                source_path = named_candidate
            else:
                candidates = sorted(
                    candidate
                    for candidate in source_path.iterdir()
                    if candidate.is_file()
                )
                if len(candidates) == 1:
                    source_path = candidates[0]
                elif not candidates:
                    raise FileNotFoundError(f"В каталоге нет файлов: {source_path}")
                else:
                    names = ", ".join(candidate.name for candidate in candidates[:20])
                    raise input_request(
                        f"В каталоге несколько файлов ({names}). Укажите filename.",
                        "filename",
                        "Имя файла Library",
                    )
        if not source_path.is_file():
            raise FileNotFoundError(f"Файл не найден: {source_path}")
        return runtime.add_file_contents(
            filename=requested_filename or source_path.name,
            contents=source_path.read_bytes(),
            content_type=str(config.get("content_type") or "") or None,
            use_in_context=bool(config.get("active", True)),
            source="runtime_control",
        )
    if operation == "library_import":
        resource_id = require_id(
            str(config.get("resource_id") or ""),
            "resource_id",
            "ID прикреплённого ресурса",
        )
        return runtime.import_resource(
            resource_id,
            use_in_context=bool(config.get("active", True)),
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
