from __future__ import annotations
from typing import Dict, Any, List
import time

class ToolLearningService:
    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    def record(self, tool_name: str, ok: bool, meta: Dict[str, Any] | None = None) -> None:
        self.events.append({
            "tool_name": tool_name,
            "ok": ok,
            "meta": meta or {},
            "timestamp": time.time(),
        })

    def stats(self) -> Dict[str, Any]:
        grouped: Dict[str, Dict[str, int]] = {}
        for e in self.events:
            g = grouped.setdefault(e["tool_name"], {"runs": 0, "ok": 0, "fail": 0})
            g["runs"] += 1
            g["ok" if e["ok"] else "fail"] += 1
        return grouped
