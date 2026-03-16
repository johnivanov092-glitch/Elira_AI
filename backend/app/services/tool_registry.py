from __future__ import annotations
from typing import Callable, Dict, Any

class ToolRegistry:
    def __init__(self):
        self.tools: Dict[str, Callable[..., Any]] = {}

    def register(self, name: str, fn: Callable[..., Any]) -> None:
        self.tools[name] = fn

    def list_tools(self):
        return sorted(self.tools.keys())

    def run(self, name: str, **kwargs):
        if name not in self.tools:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        try:
            data = self.tools[name](**kwargs)
            return {"ok": True, "tool": name, "data": data}
        except Exception as e:
            return {"ok": False, "tool": name, "error": str(e)}
