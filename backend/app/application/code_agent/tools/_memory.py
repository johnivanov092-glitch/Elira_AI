"""Long-term user memory and the curated Library as two small tools.

`memory` owns user facts (search/list/add/delete) in the existing memory store;
`library` reads documents the user curated in Settings → Library. Adding,
removing and bookmarking Library files stay UI actions.
"""
from __future__ import annotations

import json
from typing import Any

_MEMORY_ACTIONS = ("search", "list", "add", "delete")
_LIBRARY_ACTIONS = ("search", "read")


def _fact_line(item: dict[str, Any]) -> str:
    from app.application.memory.policy import is_authoritative_fact, is_volatile_fact

    text = str(item.get("text") or "").strip()
    if len(text) > 300:
        text = text[:300] + " [...]"
    category = str(item.get("category") or "fact")
    notes = []
    if category == "volatile_fact" or is_volatile_fact(text):
        notes.append("нужна live-проверка")
    if not is_authoritative_fact(item):
        notes.append("заметка, не слова пользователя")
    suffix = f" ({'; '.join(notes)})" if notes else ""
    return f"- id={item.get('id')} [{category}]{suffix} {text}"


def _add_fact(text: str, *, correction: bool, replaces_id: int | str | None) -> dict[str, Any]:
    from app.application import memory as mem
    from app.application.memory.policy import is_user_memory_source
    from app.application.memory.tool_provenance import tool_memory_provenance

    if len(text) < 3:
        return {"ok": False, "error": "text_too_short", "text": "ERROR: нечего запоминать — текст слишком короткий."}
    if correction and replaces_id in (None, ""):
        return {"ok": False, "error": "id_required",
                "text": "ERROR: для поправки укажи id заменяемой записи (найди её через memory(action='search'))."}
    provenance = tool_memory_provenance(text, correction=correction)
    res = mem.add_fact(
        text,
        category="user_fact",
        **provenance,
        importance=10 if correction else 8,
        replaces_id=replaces_id if correction else None,
    )
    if not res.get("ok"):
        return {"ok": False, "error": "memory_failed",
                "text": f"ERROR: не удалось сохранить: {res.get('error', 'ошибка памяти')}"}
    source = res.get("source", provenance["source"])
    metadata = {"id": res.get("id"), "action": res.get("action"),
                "source": source, "source_ref": res.get("source_ref", provenance["source_ref"])}
    if res.get("category") == "volatile_fact":
        return {"ok": True, **metadata,
                "text": f"Запомнила как временное состояние (перед использованием нужна live-проверка): {text}"}
    origin = "слова пользователя" if is_user_memory_source(source) else "заметка агента, требует проверки"
    return {"ok": True, **metadata, "text": f"Запомнила (id={res.get('id')}; {origin}): {res.get('text', text)}"}


def tool_memory(
    *,
    action: str,
    query: str = "",
    text: str = "",
    id: int | str | None = None,  # noqa: A002 - the model-facing argument name
    correction: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    """Search, list, add or delete long-term facts about the user."""
    from app.application import memory as mem

    act = str(action or "").strip().lower()
    limit = max(1, min(int(limit or 10), 50))
    if act not in _MEMORY_ACTIONS:
        return {"ok": False, "error": "unknown_action",
                "text": f"ERROR: action должен быть одним из {', '.join(_MEMORY_ACTIONS)}."}
    try:
        if act == "add":
            return _add_fact(str(text or query or "").strip(), correction=bool(correction), replaces_id=id)
        if act == "delete":
            if id in (None, ""):
                return {"ok": False, "error": "id_required",
                        "text": "ERROR: укажи id записи (найди её через memory(action='search'))."}
            res = mem.delete_fact(id)
            if not res.get("ok", True):
                return {"ok": False, "error": "delete_failed", "text": f"ERROR: {res.get('error', 'запись не удалена')}"}
            return {"ok": True, "id": id, "text": f"Удалила запись id={id}."}
        if act == "search":
            if not str(query or "").strip():
                return {"ok": False, "error": "query_required", "text": "ERROR: укажи query для поиска."}
            items = mem.search_facts(str(query), limit=limit).get("items") or []
            if not items:
                return {"ok": True, "items": 0,
                        "text": f"Ничего не найдено по «{query}». Посмотри memory(action='list')."}
            return {"ok": True, "items": len(items),
                    "text": "\n".join([f"Найдено {len(items)}:"] + [_fact_line(i) for i in items])}
        items = mem.list_facts(limit=limit).get("items") or []
        if not items:
            return {"ok": True, "items": 0, "text": "В памяти пока нет записей."}
        return {"ok": True, "items": len(items),
                "text": "\n".join([f"Последние {len(items)}:"] + [_fact_line(i) for i in items])}
    except Exception as exc:  # noqa: BLE001 - memory failure must not break the run
        return {"ok": False, "error": "memory_failed", "text": f"ERROR: память недоступна: {exc}"}


def tool_library(
    *,
    action: str,
    query: str = "",
    id: int | str | None = None,  # noqa: A002 - the model-facing argument name
    offset: int = 0,
) -> dict[str, Any]:
    """Search the user's Library or read one of its documents by id."""
    from app.application.library import runtime as library

    act = str(action or "").strip().lower()
    if act not in _LIBRARY_ACTIONS:
        return {"ok": False, "error": "unknown_action",
                "text": f"ERROR: action должен быть одним из {', '.join(_LIBRARY_ACTIONS)}."}
    try:
        if act == "read":
            if id in (None, ""):
                return {"ok": False, "error": "id_required",
                        "text": "ERROR: укажи id документа из library(action='search')."}
            res = library.read_library_file(int(id), offset=max(0, int(offset or 0)))
            if not res.get("ok"):
                if res.get("error") == "processing_required":
                    ref = json.dumps(res["resource"], ensure_ascii=False)
                    return {**res, "text": f"{res['text']}\nResourceRef: {ref}"}
                if res.get("error") == "resource_unavailable":
                    return res
                return {"ok": False, "error": str(res.get("error") or "read_failed"),
                        "text": f"ERROR: документ id={id} не прочитан: {res.get('error')}"}
            more = f"\n[... продолжение: offset={res['next_offset']}]" if res.get("has_more") else ""
            return {"ok": True, "file_id": res.get("file_id"),
                    "text": f"[Библиотека: {res.get('name')}; символы {res.get('offset')}–"
                            f"{int(res.get('offset') or 0) + len(str(res.get('text') or ''))} из "
                            f"{res.get('content_chars')}]\n{res.get('text') or ''}{more}"}
        res = library.search_files(str(query or ""), limit=20)
        items = res.get("items") or []
        if not items:
            return {"ok": True, "items": 0,
                    "text": f"В Библиотеке ничего не найдено по «{query}». Попробуй отдельные слова."}
        lines = [f"Найдено {len(items)} (читай library(action='read', id=…)):"]
        for item in items:
            excerpt = str(item.get("excerpt") or "").replace("\n", " ").strip()
            lines.append(f"- id={item.get('id')} {item.get('name')} ({item.get('content_chars')} симв.): {excerpt[:300]}")
        return {"ok": True, "items": len(items), "text": "\n".join(lines)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "library_failed", "text": f"ERROR: Библиотека недоступна: {exc}"}


__all__ = ["tool_library", "tool_memory"]
