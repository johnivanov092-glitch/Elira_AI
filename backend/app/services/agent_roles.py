from __future__ import annotations
from typing import Dict, Any, List

class AgentRoles:
    def planner(self, query: str) -> Dict[str, Any]:
        q = query.lower()
        if any(k in q for k in ["найди", "search", "docs", "документац", "research"]):
            route = "research"
        elif any(k in q for k in ["код", "python", "исправ", "refactor", "bug"]):
            route = "code"
        elif any(k in q for k in ["помнишь", "memory", "remember"]):
            route = "memory"
        else:
            route = "chat"
        return {"route": route, "steps": ["plan", route, "review"]}

    def researcher(self, query: str, sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "role": "researcher",
            "summary": f"Collected {len(sources)} sources for query: {query}",
            "sources": sources[:10],
        }

    def coder(self, query: str, project_context: str = "") -> Dict[str, Any]:
        return {
            "role": "coder",
            "task": query,
            "project_context": project_context[:4000],
        }

    def reviewer(self, draft: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "role": "reviewer",
            "verdict": "ok",
            "notes": "Draft reviewed. Check tool outputs and source grounding.",
            "draft": draft,
        }

    def orchestrator(self, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"role": "orchestrator", "timeline": steps, "status": "ok"}
