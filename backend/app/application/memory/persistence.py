from __future__ import annotations

from typing import Any

from app.application.memory.web_knowledge import build_web_knowledge_records
from app.core.memory import add_kb_record, add_memory, record_web_learning_run


def persist_web_knowledge(
    query: str,
    web_context: str,
    profile_name: str,
    source_kind: str = "web_search",
    url: str = "",
    title: str = "",
) -> dict[str, Any]:
    saved_memory = 0
    saved_kb = 0
    records = build_web_knowledge_records(
        query=query,
        web_context=web_context,
        source_kind=source_kind,
    )

    for record in records:
        content = record.get("content", "")
        record_url = record.get("url") or url
        record_title = record.get("title") or title or query
        record_type = record.get("type", "web_chunk")

        if add_memory(
            content,
            source=f"{source_kind}:{record_url or query[:80]}",
            memory_type=record_type,
            profile_name=profile_name,
        ):
            saved_memory += 1

        if add_kb_record(
            content=content,
            title=record_title,
            url=record_url,
            source=source_kind,
            chunk_type=record_type,
            profile_name=profile_name,
        ):
            saved_kb += 1

    record_web_learning_run(
        query=query,
        url=url,
        title=title or query[:300],
        source_kind=source_kind,
        ok=bool(web_context and web_context.strip()),
        saved_kb=saved_kb,
        saved_memory=saved_memory,
        notes=(web_context or "")[:1200],
        profile_name=profile_name,
    )
    return {
        "saved_memory": saved_memory,
        "saved_kb": saved_kb,
        "records": len(records),
    }
