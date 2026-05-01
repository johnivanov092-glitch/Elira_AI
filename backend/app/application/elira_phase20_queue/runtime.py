from __future__ import annotations

from datetime import datetime
from typing import List


def build_preview_queue(goal: str, targets: List[str]) -> dict:
    items = []
    for index, path in enumerate(targets):
        items.append({
            "order": index + 1,
            "path": path,
            "status": "queued",
            "mode": "preview",
        })

    return {
        "status": "ok",
        "goal": goal,
        "count": len(items),
        "items": items,
        "created_at": datetime.utcnow().isoformat(),
    }
