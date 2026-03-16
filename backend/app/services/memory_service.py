from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"
MEMORY_FILE = DATA_DIR / "memory_store.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_store() -> dict[str, list[dict[str, Any]]]:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write_store(data: dict[str, list[dict[str, Any]]]) -> None:
    MEMORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_profiles() -> dict[str, Any]:
    store = _read_store()
    items = []
    for profile, records in store.items():
        items.append({
            "name": profile,
            "count": len(records),
        })
    items.sort(key=lambda x: x["name"].lower())
    return {"ok": True, "profiles": items, "count": len(items)}


def list_memories(profile: str) -> dict[str, Any]:
    store = _read_store()
    records = list(reversed(store.get(profile, [])))
    return {"ok": True, "profile": profile, "items": records, "count": len(records)}


def add_memory(profile: str, text: str, source: str = "manual") -> dict[str, Any]:
    store = _read_store()
    record = {
        "id": f"{profile}-{int(datetime.utcnow().timestamp() * 1000)}",
        "text": text.strip(),
        "source": source.strip() or "manual",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    store.setdefault(profile, []).append(record)
    _write_store(store)
    return {"ok": True, "profile": profile, "item": record}


def delete_memory(profile: str, item_id: str) -> dict[str, Any]:
    store = _read_store()
    records = store.get(profile, [])
    filtered = [r for r in records if r.get("id") != item_id]
    store[profile] = filtered
    _write_store(store)
    return {"ok": True, "profile": profile, "deleted_id": item_id}


def search_memory(profile: str, query: str, limit: int = 10) -> dict[str, Any]:
    q = query.strip().lower()
    store = _read_store()
    records = store.get(profile, [])

    scored: list[tuple[int, dict[str, Any]]] = []
    for rec in records:
        text = str(rec.get("text", ""))
        low = text.lower()
        score = 0
        for token in q.split():
            if token and token in low:
                score += 1
        if score > 0:
            scored.append((score, rec))

    scored.sort(key=lambda x: (-x[0], x[1].get("created_at", "")), reverse=False)
    items = [rec for _, rec in scored[: max(1, limit)]]
    return {"ok": True, "profile": profile, "query": query, "items": items, "count": len(items)}


def build_memory_context(profile: str, query: str, limit: int = 5) -> str:
    result = search_memory(profile, query, limit)
    items = result.get("items", [])
    if not items:
        return ""
    lines = []
    for idx, item in enumerate(items, start=1):
        lines.append(f"{idx}. {item.get('text', '')}")
    return "\n".join(lines)
