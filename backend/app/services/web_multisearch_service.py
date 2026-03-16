from __future__ import annotations

from typing import Any

from app.services.tool_service import run_tool


class WebMultiSearchService:
    """
    Stage 3 multi-search wrapper.

    Runs several query variants and merges/deduplicates the results.
    """

    def _query_variants(self, query: str) -> list[str]:
        q = (query or "").strip()
        if not q:
            return []

        variants = [
            q,
            f"{q} documentation",
            f"{q} official docs",
            f"{q} guide",
        ]
        return list(dict.fromkeys(variants))

    def search(self, query: str, max_results: int = 5) -> dict[str, Any]:
        variants = self._query_variants(query)
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()

        for variant in variants:
            result = run_tool("research_web", {"query": variant, "max_results": max_results})
            items = result.get("results", []) if isinstance(result, dict) else []

            for item in items:
                if not isinstance(item, dict):
                    continue
                key = (item.get("url") or item.get("title") or item.get("snippet") or "").strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)

        context = "\n".join(
            f"- {item.get('title', '')}: {item.get('snippet', '')}"
            for item in merged[:8]
        )

        return {
            "ok": True,
            "query": query,
            "variants": variants,
            "results": merged[: max_results * 3],
            "count": len(merged[: max_results * 3]),
            "context": context,
        }
