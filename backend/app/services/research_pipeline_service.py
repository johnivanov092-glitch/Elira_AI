from __future__ import annotations

import re
import time
import uuid
from typing import Any, Dict, List, Optional


class ResearchPipelineService:
    def __init__(self, tool_service=None, run_trace_service=None, event_bus=None) -> None:
        self.tool_service = tool_service
        self.run_trace_service = run_trace_service
        self.event_bus = event_bus

    async def run(
        self,
        query: str,
        multi_search: bool = True,
        dedupe: bool = True,
        documentation_mode: bool = False,
        max_results: int = 12,
    ) -> dict:
        started = time.time()
        queries = self._rewrite_queries(query, multi_search=multi_search, documentation_mode=documentation_mode)
        raw_results = []

        for q in queries:
            results = await self._search(q)
            raw_results.append({
                "query": q,
                "results": results,
            })

        flat = self._flatten_results(raw_results)
        if dedupe:
            flat = self._dedupe(flat)

        ranked = self._rank(flat, original_query=query, documentation_mode=documentation_mode)
        ranked = ranked[:max_results]
        summary = self._summarize(ranked, query=query, documentation_mode=documentation_mode)

        payload = {
            "id": str(uuid.uuid4()),
            "query": query,
            "queries": queries,
            "documentation_mode": documentation_mode,
            "results_count": len(ranked),
            "results": ranked,
            "summary": summary,
            "duration_seconds": round(time.time() - started, 4),
        }

        await self._persist(query, payload)
        return payload

    def _rewrite_queries(self, query: str, multi_search: bool, documentation_mode: bool) -> List[str]:
        base = query.strip()
        if not multi_search:
            return [base]

        variants = [base]
        variants.append(f"{base} architecture")
        variants.append(f"{base} implementation")
        if documentation_mode:
            variants.append(f"{base} documentation")
            variants.append(f"{base} examples")
        else:
            variants.append(f"{base} code")
        return list(dict.fromkeys(variants))

    async def _search(self, query: str) -> List[dict]:
        if self.tool_service and hasattr(self.tool_service, "run_tool"):
            try:
                value = self.tool_service.run_tool("research_web", {"query": query})
                value = await self._maybe_await(value)
                if isinstance(value, dict) and "results" in value:
                    return value["results"]
                if isinstance(value, list):
                    return value
            except Exception:
                pass

        return [
            {
                "title": f"Placeholder result for: {query}",
                "url": None,
                "snippet": "Connect tool_service.run_tool('research_web', ...) for real research.",
                "source": "placeholder",
            }
        ]

    def _flatten_results(self, raw_results: List[dict]) -> List[dict]:
        flat = []
        for item in raw_results:
            q = item.get("query")
            for result in item.get("results", []):
                enriched = dict(result)
                enriched["query"] = q
                flat.append(enriched)
        return flat

    def _dedupe(self, results: List[dict]) -> List[dict]:
        seen = set()
        unique = []
        for item in results:
            key = (
                (item.get("url") or "").strip().lower(),
                (item.get("title") or "").strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _rank(self, results: List[dict], original_query: str, documentation_mode: bool) -> List[dict]:
        words = [w.lower() for w in re.findall(r"\w+", original_query)]
        ranked = []
        for item in results:
            hay = " ".join([
                str(item.get("title", "")),
                str(item.get("snippet", "")),
                str(item.get("query", "")),
            ]).lower()
            score = sum(hay.count(w) for w in words)
            if documentation_mode:
                score += 2 * hay.count("doc")
                score += 2 * hay.count("example")
            ranked.append({**item, "score": score})
        ranked.sort(key=lambda x: (-x["score"], str(x.get("title", ""))))
        return ranked

    def _summarize(self, results: List[dict], query: str, documentation_mode: bool) -> dict:
        return {
            "query": query,
            "mode": "documentation" if documentation_mode else "research",
            "top_titles": [r.get("title") for r in results[:5]],
            "notes": [
                "Used query rewriting",
                "Applied ranking",
                "Applied dedupe" if len(results) > 0 else "No dedupe needed",
            ],
        }

    async def _persist(self, query: str, payload: dict) -> None:
        if self.run_trace_service and hasattr(self.run_trace_service, "create_run"):
            try:
                run = self.run_trace_service.create_run(query, source="research_pipeline")
                self.run_trace_service.add_artifact(run["id"], "research_result", "json", payload)
                self.run_trace_service.update_status(run["id"], "completed", summary=f"Research completed for: {query}")
            except Exception:
                pass

        if self.event_bus:
            try:
                await self.event_bus.publish("research.completed", {"query": query, "results_count": payload["results_count"]})
            except Exception:
                pass

    async def _maybe_await(self, value: Any) -> Any:
        if hasattr(value, "__await__"):
            return await value
        return value
