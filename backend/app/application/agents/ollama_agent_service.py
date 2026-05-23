"""Local Ollama agent service — business logic for /api/project-brain.

Extracted from api/routes/project_brain.py.  The route file is now a thin
HTTP layer; all state, helpers, and execution logic live here.
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.infrastructure.files.file_extractor import extract_docx, extract_pdf
from app.infrastructure.runtime.ollama_client import call_ollama_json, fetch_ollama_tags, pick_model
from app.infrastructure.search.ddg_scraper import collect_web_context

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(".").resolve()
UPLOAD_ROOT = PROJECT_ROOT / "data" / "chat_uploads_tmp"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
TMP_UPLOAD_TTL_SECONDS = 24 * 60 * 60

EXCLUDED_PARTS = {
    ".git", ".idea", ".vscode", "node_modules", "target", "__pycache__",
    ".venv", "venv", "dist", "build", ".next", ".turbo", ".cache",
    "coverage", "tmp", "temp", "logs", ".elira_chat_uploads",
    "data/chat_uploads_tmp",
}
TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt",
    ".yaml", ".yml", ".toml", ".rs", ".css", ".scss", ".html", ".htm",
    ".sql", ".sh", ".bat", ".ps1", ".ini", ".cfg", ".conf", ".env", ".example",
    ".xml", ".csv",
}
TEXT_NAMES = {"Dockerfile", "Makefile", ".gitignore"}
MAX_READ_BYTES = 512 * 1024
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
MAX_AGENT_FILE_BYTES = 256 * 1024

# ---------------------------------------------------------------------------
# Process-level state (in-memory, intentionally ephemeral)
# ---------------------------------------------------------------------------

CHAT_SESSIONS: dict[str, dict[str, Any]] = {}
ATTACHMENT_INDEX: dict[str, dict[str, Any]] = {}

LEGACY_AGENT_CATALOG = [
    {"id": "chat_agent",       "title": "Chat agent",           "kind": "conversation"},
    {"id": "planner_agent",    "title": "Planner agent",        "kind": "planning"},
    {"id": "browser_agent",    "title": "Browser agent",        "kind": "research"},
    {"id": "coder_agent",      "title": "Coder agent",          "kind": "code"},
    {"id": "task_graph",       "title": "Task graph",           "kind": "orchestration"},
    {"id": "multi_agent",      "title": "Multi-agent",          "kind": "orchestration"},
    {"id": "reflection_v2",    "title": "Reflection v2",        "kind": "quality"},
    {"id": "self_improve",     "title": "Self-improving agent", "kind": "quality"},
    {"id": "terminal",         "title": "Terminal",             "kind": "tool"},
    {"id": "image_generation", "title": "Image generation",     "kind": "media"},
]

# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def is_allowed(path: Path) -> bool:
    return not bool(set(path.parts) & EXCLUDED_PARTS)


def normalize_relative_path(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise HTTPException(status_code=400, detail="Path is required")
    normalized = raw_path.replace("\\", "/").strip().lstrip("/")
    rel = Path(normalized)
    if rel.is_absolute():
        raise HTTPException(status_code=400, detail="Absolute paths are not allowed")
    if ".." in rel.parts:
        raise HTTPException(status_code=400, detail="Parent traversal is not allowed")
    return rel


def resolve_project_file(raw_path: str) -> tuple[Path, Path]:
    rel = normalize_relative_path(raw_path)
    full = (PROJECT_ROOT / rel).resolve()
    try:
        rel_from_root = full.relative_to(PROJECT_ROOT)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Path escapes project root") from exc
    if not is_allowed(rel_from_root):
        raise HTTPException(status_code=403, detail="Path is excluded")
    if not full.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not full.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    return full, rel_from_root


def looks_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES


def read_text_file(full_path: Path) -> tuple[str, str, bytes]:
    raw = full_path.read_bytes()
    if b"\x00" in raw:
        raise HTTPException(status_code=415, detail="Binary files are not supported")
    try:
        content = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
        encoding = "utf-8/replace"
    return content, encoding, raw


def hash_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Attachment management
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name or "attachment")
    return cleaned[:120] or "attachment"


def extract_upload_text(filename: str, data: bytes) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_SUFFIXES or suffix in {".pyw", ".log"}:
        try:
            return data.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace"), "utf-8/replace"
    if suffix == ".docx":
        return extract_docx(data), "docx-text"
    if suffix == ".pdf":
        return extract_pdf(data), "pdf-text"
    return "", "binary"


def cleanup_stale_temp_uploads() -> None:
    now = time.time()
    stale_ids: list[str] = []
    for attachment_id, item in list(ATTACHMENT_INDEX.items()):
        created_at = float(item.get("created_at") or 0)
        path_str = item.get("path") or ""
        if not created_at or (now - created_at) <= TMP_UPLOAD_TTL_SECONDS:
            continue
        try:
            p = Path(path_str)
            if p.exists() and p.is_file():
                p.unlink()
        except Exception:
            pass
        stale_ids.append(attachment_id)
    for attachment_id in stale_ids:
        ATTACHMENT_INDEX.pop(attachment_id, None)
    for path in UPLOAD_ROOT.glob("*"):
        try:
            if path.is_file() and (now - path.stat().st_mtime) > TMP_UPLOAD_TTL_SECONDS:
                path.unlink()
        except Exception:
            pass


def store_attachment(filename: str, data: bytes, source: str = "upload") -> dict[str, Any]:
    cleanup_stale_temp_uploads()
    attachment_id = uuid.uuid4().hex[:16]
    safe_name = _safe_filename(filename)
    disk_path = UPLOAD_ROOT / f"{attachment_id}_{safe_name}"
    disk_path.write_bytes(data)
    text, encoding = extract_upload_text(filename, data)
    item = {
        "id": attachment_id,
        "name": filename,
        "safe_name": safe_name,
        "size": len(data),
        "suffix": Path(filename).suffix.lower(),
        "path": str(disk_path),
        "source": source,
        "encoding": encoding,
        "text": text[:40_000],
        "text_available": bool(text.strip()),
        "sha256": hash_bytes(data),
        "created_at": time.time(),
    }
    ATTACHMENT_INDEX[attachment_id] = item
    return item


def attachment_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "name": item["name"],
        "size": item["size"],
        "suffix": item["suffix"],
        "source": item["source"],
        "text_available": item["text_available"],
        "preview": item.get("text", "")[:1200],
    }


def project_file_snapshot() -> list[dict[str, Any]]:
    files = []
    for p in PROJECT_ROOT.rglob("*"):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(PROJECT_ROOT)
        except Exception:
            continue
        if not is_allowed(rel):
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        files.append({
            "path": str(rel).replace("\\", "/"),
            "name": p.name,
            "suffix": p.suffix.lower(),
            "size": stat.st_size,
        })
    files.sort(key=lambda x: x["path"])
    return files


def attach_project_file(path: str) -> dict[str, Any]:
    full_path, rel_path = resolve_project_file(path)
    if not looks_text_file(full_path):
        raise HTTPException(status_code=415, detail="Only text-like project files can be attached")
    if full_path.stat().st_size > MAX_READ_BYTES:
        raise HTTPException(status_code=413, detail="Project file is too large")
    content, _, raw = read_text_file(full_path)
    item = {
        "id": uuid.uuid4().hex[:16],
        "name": str(rel_path).replace("\\", "/"),
        "size": len(raw),
        "suffix": full_path.suffix.lower(),
        "path": str(full_path),
        "source": "project",
        "encoding": "utf-8",
        "text": content[:40000],
        "text_available": True,
        "sha256": hash_bytes(raw),
        "created_at": time.time(),
        "project_path": str(rel_path).replace("\\", "/"),
    }
    ATTACHMENT_INDEX[item["id"]] = item
    return item


# ---------------------------------------------------------------------------
# Task routing + web search
# ---------------------------------------------------------------------------

def route_task(
    text: str,
    mode: str,
    attachments: list[dict[str, Any]],
    selected_project_paths: list[str],
    web_enabled: bool,
) -> dict[str, Any]:
    if mode and mode != "auto":
        return {"mode": mode, "reason": "manual"}
    low = (text or "").lower()
    if any(x in low for x in ["image", "png", "sdxl", "flux"]):
        return {"mode": "image", "reason": "image markers"}
    if selected_project_paths or any(
        a.get("suffix") in {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".css", ".html"}
        for a in attachments
    ):
        if any(x in low for x in ["fix", "refactor", "patch", "endpoint", "api", "bug"]):
            return {"mode": "code", "reason": "code markers"}
    if any(x in low for x in ["roadmap", "strategy"]):
        return {"mode": "plan", "reason": "plan markers"}
    if attachments:
        return {"mode": "analyze", "reason": "attachments present"}
    if web_enabled and any(x in low for x in ["latest", "search", "web", "research", "news"]):
        return {"mode": "research", "reason": "research markers"}
    return {"mode": "chat", "reason": "default"}



# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def read_reference_context(paths: list[str], selected_path: str | None = None) -> list[dict[str, str]]:
    context_items: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in paths[:12]:
        if not path or path == selected_path or path in seen:
            continue
        seen.add(path)
        try:
            full_path, rel_path = resolve_project_file(path)
        except HTTPException:
            continue
        if not looks_text_file(full_path):
            continue
        try:
            size = full_path.stat().st_size
        except OSError:
            continue
        if size > MAX_AGENT_FILE_BYTES:
            continue
        try:
            text, _, _ = read_text_file(full_path)
        except HTTPException:
            continue
        context_items.append({"path": str(rel_path).replace("\\", "/"), "content": text[:20_000]})
    return context_items


def build_code_prompt(
    goal: str, selected_path: str, selected_content: str, refs: list[dict[str, str]]
) -> str:
    refs_blob = "\n\n".join(f"FILE: {x['path']}\n{x['content']}" for x in refs)
    return (
        f"TASK:\n{goal}\n\n"
        f"TARGET FILE:\n{selected_path}\n\n"
        f"CURRENT CONTENT:\n{selected_content[:40000]}\n\n"
        f"REFERENCE FILES:\n{refs_blob[:30000]}\n\n"
        "Return a concrete update for the target file only."
    )


def build_chat_prompt(
    message: str,
    route_mode: str,
    attachments: list[dict[str, Any]],
    project_refs: list[dict[str, str]],
    web_results: list[dict[str, str]],
) -> str:
    attachment_blob = "\n\n".join(
        f"ATTACHMENT: {a['name']}\n{a.get('text', '')[:5000]}"
        for a in attachments if a.get("text_available")
    )
    refs_blob = "\n\n".join(f"PROJECT FILE: {x['path']}\n{x['content'][:5000]}" for x in project_refs)
    web_blob = "\n\n".join(
        f"WEB RESULT: {x['title']}\nURL: {x['url']}\nSNIPPET: {x['snippet']}\nPAGE: {x['page_text'][:3000]}"
        for x in web_results
    )
    return (
        f"USER TASK:\n{message}\n\n"
        f"ROUTE MODE: {route_mode}\n\n"
        f"ATTACHMENTS:\n{attachment_blob[:20000]}\n\n"
        f"PROJECT REFERENCES:\n{refs_blob[:20000]}\n\n"
        f"WEB RESULTS:\n{web_blob[:12000]}\n\n"
        "Respond for the chosen mode and keep it practical."
    )


# ---------------------------------------------------------------------------
# High-level execution – private helpers
# ---------------------------------------------------------------------------


def _resolve_code_target(
    attachments: list[dict],
    selected_project_paths: list[str],
) -> dict | None:
    """Return the best code target from attachments or selected paths, or None."""
    target = next(
        (item for item in attachments if item.get("source") == "project" and item.get("project_path")),
        None,
    )
    if target is None and selected_project_paths:
        try:
            full_path, rel_path = resolve_project_file(selected_project_paths[0])
            content, _, _ = read_text_file(full_path)
            target = {"project_path": str(rel_path).replace("\\", "/"), "text": content}
        except HTTPException:
            target = None
    return target


def _build_code_response(
    result: dict,
    route: dict,
    model: str,
    session_id: str,
    attachments: list[dict],
    selected_project_paths: list[str],
    web_results: list,
    target_path: str,
) -> dict[str, Any]:
    """Assemble the JSON response dict for code-mode requests."""
    return {
        "status": "ok",
        "session_id": session_id,
        "model": model,
        "route": route,
        "answer": str(result.get("answer", "")),
        "plan": result.get("plan") if isinstance(result.get("plan"), list) else [],
        "attachment_summaries": [attachment_summary(x) for x in attachments],
        "selected_project_paths": selected_project_paths,
        "web_results": web_results,
        "agents_used": ["coder_agent", "reflection_v2"],
        "code_suggestion": {
            "target_path": str(result.get("target_path") or target_path),
            "updated_content": str(result.get("updated_content") or ""),
            "notes": str(result.get("notes") or ""),
        },
    }


def _handle_code_mode(
    message: str,
    model: str,
    selected_project_paths: list[str],
    attachments: list[dict],
    actual_session_id: str,
    route: dict,
    session: dict,
    web_results: list,
) -> tuple[dict[str, Any] | None, dict]:
    """Attempt code-mode execution; update route when no target file is available.

    Returns (response | None, route).
    If response is not None the caller should return it immediately.
    If response is None no suitable file was found and route has been set to 'analyze'.
    """
    target = _resolve_code_target(attachments, selected_project_paths)
    if target is None:
        return None, {"mode": "analyze", "reason": "no project file attached for code mode"}

    refs = read_reference_context(selected_project_paths, selected_path=target["project_path"])
    system_prompt = (
        "You are Elira coder agent. Return JSON only with keys: answer, plan, target_path, "
        "updated_content, notes. updated_content must contain the full replacement file content "
        "for target_path. Do not return markdown."
    )
    result = call_ollama_json(
        model, system_prompt,
        build_code_prompt(message, target["project_path"], target["text"], refs),
    )
    response = _build_code_response(
        result, route, model, actual_session_id, attachments,
        selected_project_paths, web_results, target["project_path"],
    )
    session["messages"].append({"role": "user", "content": message, "ts": time.time()})
    session["messages"].append({"role": "assistant", "content": response["answer"], "ts": time.time(), "route": route})
    return response, route


# ---------------------------------------------------------------------------
# High-level execution
# ---------------------------------------------------------------------------

def _build_chat_send_response(
    result: dict,
    route: dict,
    model: str,
    session_id: str,
    attachments: list[dict],
    selected_project_paths: list[str],
    web_results: list,
) -> dict[str, Any]:
    """Assemble the chat-mode JSON response dict."""
    answer = str(result.get("answer") or "") or "No response from model."
    plan = result.get("plan") if isinstance(result.get("plan"), list) else []
    return {
        "status": "ok",
        "session_id": session_id,
        "model": model,
        "route": route,
        "answer": answer,
        "plan": plan,
        "sources_note": str(result.get("sources_note") or ""),
        "suggested_agent": str(result.get("suggested_agent") or ""),
        "image_prompt": str(result.get("image_prompt") or ""),
        "attachment_summaries": [attachment_summary(x) for x in attachments],
        "selected_project_paths": selected_project_paths,
        "web_results": web_results,
        "agents_used": [x for x in [
            "planner_agent" if route["mode"] == "plan" else "chat_agent",
            "browser_agent" if web_results else None,
        ] if x],
    }


def execute_chat_send(
    *,
    message: str,
    model_hint: str | None,
    mode: str,
    web_enabled: bool,
    session_id: str | None,
    attachment_ids: list[str],
    selected_project_paths: list[str],
) -> dict[str, Any]:
    tags = fetch_ollama_tags()
    model = pick_model(model_hint, tags)
    attachments = [ATTACHMENT_INDEX[aid] for aid in attachment_ids if aid in ATTACHMENT_INDEX]
    route = route_task(message, mode, attachments, selected_project_paths, web_enabled)
    project_refs = read_reference_context(selected_project_paths)
    web_results = (
        collect_web_context(message)
        if web_enabled and route["mode"] in {"research", "chat", "analyze", "plan"}
        else []
    )
    actual_session_id = session_id or uuid.uuid4().hex[:12]
    session = CHAT_SESSIONS.setdefault(actual_session_id, {"messages": []})

    if route["mode"] == "code":
        response, route = _handle_code_mode(
            message, model, selected_project_paths, attachments,
            actual_session_id, route, session, web_results,
        )
        if response is not None:
            return response

    system_prompt = (
        "You are Elira chat-first local agent. Return JSON only with keys: answer, plan, sources_note, "
        "suggested_agent, image_prompt. For plan mode, plan should be a list of short steps. "
        "For image requests, answer briefly and fill image_prompt."
    )
    result = call_ollama_json(
        model, system_prompt,
        build_chat_prompt(message, route["mode"], attachments, project_refs, web_results),
    )
    response = _build_chat_send_response(result, route, model, actual_session_id, attachments, selected_project_paths, web_results)
    session["messages"].append({"role": "user", "content": message, "ts": time.time()})
    session["messages"].append({"role": "assistant", "content": response["answer"], "ts": time.time(), "route": route})
    return response


def execute_ollama_plan(
    *,
    model_hint: str | None,
    goal: str,
    selected_path: str,
    selected_content: str,
) -> dict[str, Any]:
    tags = fetch_ollama_tags()
    model = pick_model(model_hint, tags)
    system_prompt = "Return JSON only with keys: summary, steps, risks, selected_path. steps must be a list of short strings."
    user_prompt = f"TASK:\n{goal}\n\nFILE:\n{selected_path}\n\nCONTENT:\n{selected_content[:30000]}"
    result = call_ollama_json(model, system_prompt, user_prompt)
    return {
        "status": "ok",
        "provider": "ollama",
        "model": model,
        "summary": str(result.get("summary") or ""),
        "steps": result.get("steps") if isinstance(result.get("steps"), list) else [],
        "risks": result.get("risks") if isinstance(result.get("risks"), list) else [],
        "selected_path": selected_path,
    }


def execute_ollama_run(
    *,
    model_hint: str | None,
    goal: str,
    selected_path: str,
    selected_content: str,
    project_files: list[str],
    mode: str,
) -> dict[str, Any]:
    tags = fetch_ollama_tags()
    model = pick_model(model_hint, tags)
    refs = read_reference_context(project_files, selected_path=selected_path)
    system_prompt = (
        "You are Elira local coder agent. Return JSON only with keys: answer, plan, target_path, "
        "updated_content, notes. updated_content must be the full file content."
    )
    result = call_ollama_json(
        model, system_prompt,
        build_code_prompt(goal, selected_path, selected_content, refs),
    )
    return {
        "status": "ok",
        "provider": "ollama",
        "model": model,
        "mode": mode,
        "answer": str(result.get("answer") or ""),
        "plan": result.get("plan") if isinstance(result.get("plan"), list) else [],
        "target_path": str(result.get("target_path") or selected_path),
        "updated_content": str(result.get("updated_content") or ""),
        "notes": str(result.get("notes") or ""),
        "references_used": [item["path"] for item in refs],
    }
