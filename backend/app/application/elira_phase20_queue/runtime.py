"""Application-layer runtime for the Elira phase20 preview-queue endpoint.

No DB involved: the endpoint is stateless, building a ranked queue of
preview targets from the caller-supplied list.  The HTTP layer in
``api/routes/elira_phase20_queue.py`` is a thin FastAPI shell.
"""
from __future__ import annotations

from datetime import datetime
from typing import List


def build_preview_queue(goal: str, targets: List[str]) -> dict:
    """Build a ranked preview-queue response for the supplied target paths."""
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
