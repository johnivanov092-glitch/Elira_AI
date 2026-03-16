from __future__ import annotations

from typing import Any

from app.services.web_service import research_web


class BrowserAgent:
    """
    Lightweight browser/research agent.

    IMPORTANT:
    - Do NOT import tool_service here.
    - This avoids circular import:
      browser_agent -> tool_service -> browser_agent
    """

    def search(self, query: str, max_results: int = 5) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {
                "ok": False,
                "query": query,
                "results": [],
                "count": 0,
                "error": "Empty query",
            }

        results = research_web(query=query, max_results=max_results)
        if not isinstance(results, list):
            return results if isinstance(results, dict) else {
                "ok": False,
                "query": query,
                "results": [],
                "count": 0,
                "error": "Unexpected browser result",
            }

        context = "\n".join(
            f"- {item.get('title', '')}: {item.get('snippet', '')}"
            for item in results[:5]
            if isinstance(item, dict)
        )

        return {
            "ok": True,
            "query": query,
            "route": "browser_agent",
            "results": results,
            "count": len(results),
            "context": context,
            "meta": {
                "max_results": max_results,
                "source": "research_web",
            },
        }

    def open_docs_mode(self, query: str, max_results: int = 5) -> dict[str, Any]:
        docs_query = f"{query} official documentation"
        return self.search(docs_query, max_results=max_results)
