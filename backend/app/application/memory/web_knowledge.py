from __future__ import annotations

from app.core.files import truncate_text


def clean_browser_text(text: str) -> str:
    if not text:
        return ""

    normalized = text.replace("\r", " ").replace("\t", " ")
    while "  " in normalized:
        normalized = normalized.replace("  ", " ")
    return normalized.strip()


def chunk_browser_text(text: str, size: int = 1200) -> list[str]:
    chunks: list[str] = []
    normalized = clean_browser_text(text)

    start = 0
    while start < len(normalized):
        chunk = normalized[start:start + size]
        if chunk.strip():
            chunks.append(chunk.strip())
        start += size

    return chunks


def build_browser_rag_records(
    *,
    url: str,
    goal: str,
    summary: str,
    page_text: str,
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []

    clean_summary = clean_browser_text(summary)
    clean_page_text = clean_browser_text(page_text)

    if clean_summary:
        records.append({
            "type": "browser_summary",
            "url": url,
            "goal": goal,
            "content": clean_summary,
        })

    for chunk in chunk_browser_text(clean_page_text):
        records.append({
            "type": "browser_page",
            "url": url,
            "goal": goal,
            "content": chunk,
        })

    return records


def build_web_knowledge_records(
    *,
    query: str,
    web_context: str,
    source_kind: str = "web_search",
    max_chars: int = 14000,
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    clean_query = (query or "").strip()
    clean_text = clean_browser_text(truncate_text(web_context or "", max_chars))
    if not clean_text:
        return records

    records.append({
        "type": "web_summary",
        "url": "",
        "goal": clean_query,
        "content": f"WEB QUERY: {clean_query}\n\n{clean_text[:3000]}",
        "title": clean_query[:300],
        "source_kind": source_kind,
    })

    for chunk in chunk_browser_text(clean_text, size=1200):
        records.append({
            "type": "web_chunk",
            "url": "",
            "goal": clean_query,
            "content": chunk,
            "title": clean_query[:300],
            "source_kind": source_kind,
        })

    return records
