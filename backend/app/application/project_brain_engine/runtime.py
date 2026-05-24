# DEPRECATED: не используется ни одним роутером. Кандидат на удаление.
from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class ProjectBrainEngineService:
    def __init__(
        self,
        project_root: str = ".",
        dependency_graph_service=None,
        project_brain_service=None,
        tool_service=None,
        run_trace_service=None,
        event_bus=None,
        max_file_size: int = 250_000,
        max_index_files: int = 500,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.dependency_graph_service = dependency_graph_service
        self.project_brain_service = project_brain_service
        self.tool_service = tool_service
        self.run_trace_service = run_trace_service
        self.event_bus = event_bus
        self.max_file_size = max_file_size
        self.max_index_files = max_index_files

    async def health(self) -> dict:
        return {
            "status": "ok",
            "project_root": str(self.project_root),
            "integrations": {
                "dependency_graph_service": self.dependency_graph_service is not None,
                "project_brain_service": self.project_brain_service is not None,
                "tool_service": self.tool_service is not None,
                "run_trace_service": self.run_trace_service is not None,
                "event_bus": self.event_bus is not None,
            },
        }

    async def build_project_snapshot(self) -> dict:
        started = time.time()
        files = []
        directories = 0

        if not self.project_root.exists():
            return {
                "status": "not_found",
                "project_root": str(self.project_root),
                "files": [],
                "directories": 0,
            }

        for root, dirnames, filenames in os.walk(self.project_root):
            root_path = Path(root)
            rel_root = root_path.relative_to(self.project_root)
            directories += len(dirnames)

            for name in filenames:
                path = root_path / name
                rel_path = path.relative_to(self.project_root)
                try:
                    stat = path.stat()
                except Exception:
                    continue

                files.append({
                    "path": str(rel_path).replace("\\", "/"),
                    "suffix": path.suffix.lower(),
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                })

                if len(files) >= self.max_index_files:
                    break
            if len(files) >= self.max_index_files:
                break

        snapshot = {
            "status": "ok",
            "project_root": str(self.project_root),
            "files_count": len(files),
            "directories_count": directories,
            "files": files,
            "created_at": time.time(),
            "duration_seconds": round(time.time() - started, 4),
        }
        return snapshot

    async def build_semantic_index(self, include_extensions: Optional[List[str]] = None) -> dict:
        include_extensions = include_extensions or [".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".json", ".md"]
        documents = []
        token_count = 0

        snapshot = await self.build_project_snapshot()
        for item in snapshot.get("files", []):
            suffix = item.get("suffix", "")
            if suffix not in include_extensions:
                continue

            full_path = self.project_root / item["path"]
            if item.get("size", 0) > self.max_file_size:
                continue

            try:
                content = full_path.read_text(encoding="utf-8")
            except Exception:
                try:
                    content = full_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

            fingerprint = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
            chunks = self._chunk_text(content, chunk_size=1200)
            token_count += sum(len(chunk.split()) for chunk in chunks)

            documents.append({
                "path": item["path"],
                "fingerprint": fingerprint,
                "chunks_count": len(chunks),
                "preview": content[:300],
                "chunks": chunks[:8],
            })

        return {
            "status": "ok",
            "project_root": str(self.project_root),
            "documents_count": len(documents),
            "estimated_tokens": token_count,
            "documents": documents,
            "created_at": time.time(),
        }

    async def search_index(self, query: str, include_extensions: Optional[List[str]] = None, limit: int = 12) -> dict:
        index = await self.build_semantic_index(include_extensions=include_extensions)
        words = [w.lower() for w in query.split() if w.strip()]
        scored = []

        for doc in index.get("documents", []):
            haystack = " ".join([doc.get("path", ""), doc.get("preview", "")]).lower()
            score = sum(haystack.count(word) for word in words)
            if score > 0:
                scored.append({
                    "path": doc["path"],
                    "score": score,
                    "preview": doc.get("preview", ""),
                    "chunks_count": doc.get("chunks_count", 0),
                })

        scored.sort(key=lambda x: (-x["score"], x["path"]))
        return {
            "status": "ok",
            "query": query,
            "results": scored[:limit],
            "results_count": len(scored[:limit]),
        }

    async def analyze_project_goal(self, goal: str) -> dict:
        snapshot = await self.build_project_snapshot()
        search = await self.search_index(goal)
        dependency_context = await self._get_dependency_context(goal)

        if self.project_brain_service and hasattr(self.project_brain_service, "analyze_project"):
            try:
                result = self.project_brain_service.analyze_project(
                    goal=goal,
                    snapshot=snapshot,
                    search=search,
                    dependency_context=dependency_context,
                )
                result = await self._maybe_await(result)
                return {
                    "status": "ok",
                    "goal": goal,
                    "analysis": result,
                    "snapshot_summary": {
                        "files_count": snapshot.get("files_count", 0),
                        "directories_count": snapshot.get("directories_count", 0),
                    },
                    "matches": search.get("results", []),
                    "dependency_context": dependency_context,
                }
            except Exception:
                pass

        return {
            "status": "ok",
            "goal": goal,
            "analysis": {
                "summary": "Project brain analysis placeholder",
                "focus_areas": [
                    "routing",
                    "services",
                    "dependency graph",
                    "desktop runtime",
                    "safe patch flow",
                ],
                "recommendations": [
                    "Map hot services and route dependencies",
                    "Add semantic retrieval over project files",
                    "Connect autonomous dev engine to project_brain_service",
                ],
            },
            "snapshot_summary": {
                "files_count": snapshot.get("files_count", 0),
                "directories_count": snapshot.get("directories_count", 0),
            },
            "matches": search.get("results", []),
            "dependency_context": dependency_context,
        }

    async def create_refactor_plan(self, goal: str) -> dict:
        analysis = await self.analyze_project_goal(goal)
        matches = analysis.get("matches", [])
        files = [m["path"] for m in matches[:6]]

        plan = {
            "status": "ok",
            "goal": goal,
            "plan_id": str(uuid.uuid4()),
            "created_at": time.time(),
            "target_files": files,
            "steps": [
                "Inspect matched files and routes",
                "Review dependency impact",
                "Prepare minimal patch preview",
                "Run safe verification",
                "Apply changes only after preview approval",
            ],
            "analysis": analysis,
        }
        await self._persist_trace(goal, "project_brain.plan_created", plan)
        return plan

    async def _get_dependency_context(self, goal: str) -> dict:
        if self.dependency_graph_service:
            # Placeholder for real dependency graph service integration.
            return {
                "status": "placeholder",
                "goal": goal,
                "message": "Connect dependency graph build/reverse/hotspots here",
            }
        return {
            "status": "unavailable",
            "goal": goal,
            "message": "dependency_graph_service not configured",
        }

    async def _persist_trace(self, goal: str, event_name: str, payload: dict) -> None:
        if self.run_trace_service and hasattr(self.run_trace_service, "create_run"):
            try:
                run = self.run_trace_service.create_run(goal=goal, source="project_brain")
                self.run_trace_service.add_event(run["id"], event_name, payload)
                self.run_trace_service.add_artifact(run["id"], "project_brain_plan", "json", payload)
                self.run_trace_service.update_status(run["id"], "completed", summary=f"Project brain plan created for: {goal}")
            except Exception:
                pass

        if self.event_bus:
            try:
                await self.event_bus.publish(event_name, payload)
            except Exception:
                pass

    def _chunk_text(self, text: str, chunk_size: int = 1200) -> List[str]:
        if not text:
            return []
        chunks = []
        current = 0
        while current < len(text):
            chunks.append(text[current:current + chunk_size])
            current += chunk_size
        return chunks

    async def _maybe_await(self, value: Any) -> Any:
        if hasattr(value, "__await__"):
            return await value
        return value
