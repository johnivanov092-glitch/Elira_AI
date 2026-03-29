from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/project-brain", tags=["project-brain"])

PROJECT_ROOT = Path(".").resolve()
UPLOAD_ROOT = PROJECT_ROOT / "data" / "chat_uploads_tmp"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
TMP_UPLOAD_TTL_SECONDS = 24 * 60 * 60

EXCLUDED_PARTS = {
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
    "target",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".cache",
    "coverage",
    "tmp",
    "temp",
    "logs",
    ".elira_chat_uploads",
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
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))

CHAT_SESSIONS: dict[str, dict[str, Any]] = {}
ATTACHMENT_INDEX: dict[str, dict[str, Any]] = {}

LEGACY_AGENT_CATALOG = [
    {
        "id": "chat_agent",
        "title": "Chat agent",
        "kind": "conversation",
        "description": "Базовый диалоговый агент для обычных запросов.",
    },
    {
        "id": "planner_agent",
        "title": "Planner agent",
        "kind": "planning",
        "description": "Пошаговый план и orchestration поверх reasoning/browser/terminal.",
    },
    {
        "id": "browser_agent",
        "title": "Browser agent",
        "kind": "research",
        "description": "Веб-поиск, чтение страниц и сбор контекста для ответа.",
    },
    {
        "id": "coder_agent",
        "title": "Coder agent",
        "kind": "code",
        "description": "Локальный кодовый агент для файла, diff-preview и безопасного patch-flow.",
    },
    {
        "id": "task_graph",
        "title": "Task graph",
        "kind": "orchestration",
        "description": "Граф выполнения шагов для research/code/file режимов.",
    },
    {
        "id": "multi_agent",
        "title": "Multi-agent",
        "kind": "orchestration",
        "description": "Planner + Researcher + Coder + Reviewer + Orchestrator.",
    },
    {
        "id": "reflection_v2",
        "title": "Reflection v2",
        "kind": "quality",
        "description": "Самопроверка ответа, groundedness, completeness, retry loop.",
    },
    {
        "id": "self_improve",
        "title": "Self-improving agent",
        "kind": "quality",
        "description": "Повторное улучшение ответа после критики.",
    },
    {
        "id": "terminal",
        "title": "Terminal",
        "kind": "tool",
        "description": "Безопасный локальный терминальный контекст только для read-only анализа.",
    },
    {
        "id": "image_generation",
        "title": "Image generation",
        "kind": "media",
        "description": "Наследуемый image-flow из ранней версии Elira: routing и prompt prep для будущей генерации.",
    },
]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    model: str | None = Field(default=None, max_length=200)
    mode: str = Field(default="auto", max_length=64)
    web_enabled: bool = Field(default=True)
    session_id: str | None = Field(default=None, max_length=100)
    attachment_ids: list[str] = Field(default_factory=list)
    selected_project_paths: list[str] = Field(default_factory=list)


class LocalAgentRunRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=4000)
    selected_path: str = Field(..., min_length=1)
    selected_content: str = Field(..., min_length=1, max_length=400_000)
    model: str | None = Field(default=None, max_length=200)
    project_files: list[str] = Field(default_factory=list)
    mode: str = Field(default="patch", max_length=64)


class LocalAgentPlanRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=4000)
    selected_path: str = Field(..., min_length=1)
    selected_content: str = Field(..., min_length=1, max_length=400_000)
    model: str | None = Field(default=None, max_length=200)


# ---------- file helpers ----------
def _is_allowed(path: Path) -> bool:
    return not bool(set(path.parts) & EXCLUDED_PARTS)


def _normalize_relative_path(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise HTTPException(status_code=400, detail="Path is required")

    normalized = raw_path.replace("\\", "/").strip().lstrip("/")
    rel = Path(normalized)

    if rel.is_absolute():
        raise HTTPException(status_code=400, detail="Absolute paths are not allowed")
    if ".." in rel.parts:
        raise HTTPException(status_code=400, detail="Parent traversal is not allowed")
    return rel


def _resolve_project_file(raw_path: str) -> tuple[Path, Path]:
    rel = _normalize_relative_path(raw_path)
    full = (PROJECT_ROOT / rel).resolve()

    try:
        rel_from_root = full.relative_to(PROJECT_ROOT)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Path escapes project root") from exc

    if not _is_allowed(rel_from_root):
        raise HTTPException(status_code=403, detail="Path is excluded")
    if not full.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not full.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    return full, rel_from_root


def _looks_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return path.name in TEXT_NAMES


def _read_text_file(full_path: Path) -> tuple[str, str, bytes]:
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


def _hash_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ---------- uploads ----------
def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name or "attachment")
    return cleaned[:120] or "attachment"


def _extract_text_from_docx(data: bytes) -> str:
    try:
        with tempfile.TemporaryDirectory(prefix="elira_docx_") as tmp:
            path = Path(tmp) / "file.docx"
            path.write_bytes(data)
            with zipfile.ZipFile(path, "r") as zf:
                xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<[^>]+>", "", xml)
        return html.unescape(xml)
    except Exception:
        return ""


def _extract_text_from_pdf(data: bytes) -> str:
    text = data.decode("latin-1", errors="ignore")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^\x20-\x7E\n\rА-Яа-яЁё]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_upload_text(filename: str, data: bytes) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_SUFFIXES or suffix in {".pyw", ".log"}:
        try:
            return data.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace"), "utf-8/replace"
    if suffix == ".docx":
        return _extract_text_from_docx(data), "docx-text"
    if suffix == ".pdf":
        return _extract_text_from_pdf(data), "pdf-text"
    return "", "binary"




def _cleanup_stale_temp_uploads() -> None:
    now = time.time()
    stale_ids: list[str] = []
    for attachment_id, item in list(ATTACHMENT_INDEX.items()):
        created_at = float(item.get("created_at") or 0)
        path_str = item.get("path") or ""
        if not created_at or (now - created_at) <= TMP_UPLOAD_TTL_SECONDS:
            continue
        try:
            path = Path(path_str)
            if path.exists() and path.is_file():
                path.unlink()
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

def _store_attachment(filename: str, data: bytes, source: str = "upload") -> dict[str, Any]:
    _cleanup_stale_temp_uploads()
    attachment_id = uuid.uuid4().hex[:16]
    safe_name = _safe_filename(filename)
    disk_path = UPLOAD_ROOT / f"{attachment_id}_{safe_name}"
    disk_path.write_bytes(data)
    text, encoding = _extract_upload_text(filename, data)
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
        "sha256": _hash_bytes(data),
        "created_at": time.time(),
    }
    ATTACHMENT_INDEX[attachment_id] = item
    return item


def _attachment_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "name": item["name"],
        "size": item["size"],
        "suffix": item["suffix"],
        "source": item["source"],
        "text_available": item["text_available"],
        "preview": item.get("text", "")[:1200],
    }


# ---------- ollama ----------
def _make_json_request(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urlrequest.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with urlrequest.urlopen(req, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Ollama HTTP error: {detail}") from exc
    except urlerror.URLError as exc:
        raise HTTPException(status_code=503, detail=f"Ollama is unavailable at {OLLAMA_BASE_URL}") from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Ollama request timed out") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Invalid JSON returned by Ollama") from exc


def _fetch_ollama_tags() -> dict[str, Any]:
    return _make_json_request(f"{OLLAMA_BASE_URL}/api/tags")


def _pick_model(requested_model: str | None, tags_payload: dict[str, Any]) -> str:
    models = tags_payload.get("models") or []
    names = [item.get("name") for item in models if item.get("name")]
    if requested_model and requested_model.strip():
        return requested_model.strip()
    if DEFAULT_OLLAMA_MODEL:
        return DEFAULT_OLLAMA_MODEL
    if names:
        return names[0]
    raise HTTPException(status_code=400, detail="No Ollama models found. Pull a model first.")


def _call_ollama_json(model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {"temperature": 0.15},
    }
    result = _make_json_request(f"{OLLAMA_BASE_URL}/api/chat", payload)
    content = (((result.get("message") or {}).get("content")) or "").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if match:
            return json.loads(match.group(0))
        raise HTTPException(status_code=502, detail="Model did not return valid JSON")


# ---------- routing + web ----------
def _route_task(text: str, mode: str, attachments: list[dict[str, Any]], selected_project_paths: list[str], web_enabled: bool) -> dict[str, Any]:
    if mode and mode != "auto":
        return {"mode": mode, "reason": "manual"}

    low = (text or "").lower()
    if any(x in low for x in ["нарисуй", "изображ", "image", "картин", "png", "sdxl", "flux"]):
        return {"mode": "image", "reason": "image markers"}
    if selected_project_paths or any(a.get("suffix") in {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".css", ".html"} for a in attachments):
        if any(x in low for x in ["исправ", "fix", "refactor", "patch", "endpoint", "код", "api", "функц", "ошибк", "bug", "добавь"]):
            return {"mode": "code", "reason": "code markers"}
    if any(x in low for x in ["план", "roadmap", "шаг", "архитект", "strategy", "стратег"]):
        return {"mode": "plan", "reason": "plan markers"}
    if attachments:
        return {"mode": "analyze", "reason": "attachments present"}
    if web_enabled and any(x in low for x in ["кто", "когда", "новост", "latest", "найди", "search", "в интернете", "документац", "web", "веб", "research"]):
        return {"mode": "research", "reason": "research markers"}
    return {"mode": "chat", "reason": "default"}


def _clean_html_text(raw_html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", raw_html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _search_web(query: str, limit: int = 5) -> list[dict[str, str]]:
    url = f"https://html.duckduckgo.com/html/?q={urlparse.quote_plus(query[:300])}"
    req = urlrequest.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlrequest.urlopen(req, timeout=20) as response:
            html_text = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
        re.S,
    )
    results = []
    for match in pattern.finditer(html_text):
        href = html.unescape(match.group("href"))
        title = _clean_html_text(match.group("title"))
        snippet = _clean_html_text(match.group("snippet"))
        if href.startswith("//"):
            href = "https:" + href
        if "duckduckgo.com/l/?uddg=" in href:
            parsed = urlparse.urlparse(href)
            query_params = urlparse.parse_qs(parsed.query)
            href = urlparse.unquote(query_params.get("uddg", [href])[0])
        if href.startswith("http"):
            results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def _fetch_web_page_text(url: str) -> str:
    req = urlrequest.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlrequest.urlopen(req, timeout=20) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return ""
            raw = response.read().decode("utf-8", errors="ignore")
            text = _clean_html_text(raw)
            return text[:6000]
    except Exception:
        return ""


def _collect_web_context(query: str) -> list[dict[str, str]]:
    results = _search_web(query, limit=4)
    enriched = []
    for item in results[:3]:
        page_text = _fetch_web_page_text(item["url"])
        enriched.append({
            "title": item["title"],
            "url": item["url"],
            "snippet": item["snippet"],
            "page_text": page_text,
        })
    return enriched


# ---------- prompt builders ----------
def _read_reference_context(paths: list[str], selected_path: str | None = None) -> list[dict[str, str]]:
    context_items: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in paths[:12]:
        if not path or path == selected_path or path in seen:
            continue
        seen.add(path)
        try:
            full_path, rel_path = _resolve_project_file(path)
        except HTTPException:
            continue
        if not _looks_text_file(full_path):
            continue
        try:
            size = full_path.stat().st_size
        except OSError:
            continue
        if size > MAX_AGENT_FILE_BYTES:
            continue
        try:
            text, _, _ = _read_text_file(full_path)
        except HTTPException:
            continue
        context_items.append({"path": str(rel_path).replace("\\", "/"), "content": text[:20_000]})
    return context_items


def _build_code_prompt(goal: str, selected_path: str, selected_content: str, refs: list[dict[str, str]]) -> str:
    refs_blob = "\n\n".join(f"FILE: {x['path']}\n{x['content']}" for x in refs)
    return (
        f"TASK:\n{goal}\n\n"
        f"TARGET FILE:\n{selected_path}\n\n"
        f"CURRENT CONTENT:\n{selected_content[:40000]}\n\n"
        f"REFERENCE FILES:\n{refs_blob[:30000]}\n\n"
        "Return a concrete update for the target file only."
    )


def _build_chat_prompt(message: str, route_mode: str, attachments: list[dict[str, Any]], project_refs: list[dict[str, str]], web_results: list[dict[str, str]]) -> str:
    attachment_blob = "\n\n".join(
        f"ATTACHMENT: {a['name']}\n{a.get('text', '')[:5000]}" for a in attachments if a.get("text_available")
    )
    refs_blob = "\n\n".join(
        f"PROJECT FILE: {x['path']}\n{x['content'][:5000]}" for x in project_refs
    )
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


# ---------- routes ----------
@router.get("/status")
def project_brain_status():
    return {
        "status": "ok",
        "project_root": str(PROJECT_ROOT),
        "excluded_parts": sorted(EXCLUDED_PARTS),
        "max_read_bytes": MAX_READ_BYTES,
        "chat_upload_root": str(UPLOAD_ROOT),
    }


@router.get("/snapshot")
def project_snapshot():
    files = []
    for p in PROJECT_ROOT.rglob("*"):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(PROJECT_ROOT)
        except Exception:
            continue
        if not _is_allowed(rel):
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
    return {"status": "ok", "project_root": str(PROJECT_ROOT), "files": files, "files_count": len(files)}


@router.get("/file")
def read_project_file(path: str = Query(..., min_length=1)):
    full_path, rel_path = _resolve_project_file(path)
    if not _looks_text_file(full_path):
        raise HTTPException(status_code=415, detail="Only text-like source files are readable")
    size = full_path.stat().st_size
    if size > MAX_READ_BYTES:
        raise HTTPException(status_code=413, detail=f"File is too large to open ({size} bytes)")
    content, encoding, raw = _read_text_file(full_path)
    return {
        "status": "ok",
        "path": str(rel_path).replace("\\", "/"),
        "name": full_path.name,
        "suffix": full_path.suffix.lower(),
        "size": size,
        "encoding": encoding,
        "sha256": _hash_bytes(raw),
        "content": content,
    }


@router.get("/agent/legacy/catalog")
def legacy_agents_catalog():
    return {"status": "ok", "agents": LEGACY_AGENT_CATALOG}


@router.get("/agent/ollama/status")
def ollama_status():
    tags = _fetch_ollama_tags()
    model_names = [item.get("name") for item in tags.get("models") or [] if item.get("name")]
    default_model = _pick_model(None, tags) if model_names or DEFAULT_OLLAMA_MODEL else ""
    return {
        "status": "ok",
        "provider": "ollama",
        "base_url": OLLAMA_BASE_URL,
        "models": model_names,
        "default_model": default_model,
    }


@router.post("/chat/attachment")
async def upload_chat_attachment(file: UploadFile = File(...), source: str = Form("upload")):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail=f"Attachment too large ({len(data)} bytes)")
    item = _store_attachment(file.filename or "attachment.bin", data, source=source)
    return {"status": "ok", "attachment": _attachment_summary(item)}


@router.post("/chat/project-file")
def attach_project_file(path: str = Form(...)):
    full_path, rel_path = _resolve_project_file(path)
    if not _looks_text_file(full_path):
        raise HTTPException(status_code=415, detail="Only text-like project files can be attached")
    if full_path.stat().st_size > MAX_READ_BYTES:
        raise HTTPException(status_code=413, detail="Project file is too large")
    content, _, raw = _read_text_file(full_path)
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
        "sha256": _hash_bytes(raw),
        "created_at": time.time(),
        "project_path": str(rel_path).replace("\\", "/"),
    }
    ATTACHMENT_INDEX[item["id"]] = item
    return {"status": "ok", "attachment": _attachment_summary(item) | {"project_path": item["project_path"]}}


@router.post("/chat/send")
def chat_send(payload: ChatRequest):
    tags = _fetch_ollama_tags()
    model = _pick_model(payload.model, tags)

    attachments = [ATTACHMENT_INDEX[item_id] for item_id in payload.attachment_ids if item_id in ATTACHMENT_INDEX]
    route = _route_task(payload.message, payload.mode, attachments, payload.selected_project_paths, payload.web_enabled)
    project_refs = _read_reference_context(payload.selected_project_paths)
    web_results = _collect_web_context(payload.message) if payload.web_enabled and route["mode"] in {"research", "chat", "analyze", "plan"} else []

    session_id = payload.session_id or uuid.uuid4().hex[:12]
    session = CHAT_SESSIONS.setdefault(session_id, {"messages": []})

    if route["mode"] == "code":
        target_attachment = None
        for item in attachments:
            if item.get("source") == "project" and item.get("project_path"):
                target_attachment = item
                break
        if target_attachment is None and payload.selected_project_paths:
            try:
                full_path, rel_path = _resolve_project_file(payload.selected_project_paths[0])
                content, _, _ = _read_text_file(full_path)
                target_attachment = {"project_path": str(rel_path).replace("\\", "/"), "text": content}
            except HTTPException:
                target_attachment = None

        if target_attachment is None:
            route = {"mode": "analyze", "reason": "no project file attached for code mode"}
        else:
            refs = _read_reference_context(payload.selected_project_paths, selected_path=target_attachment["project_path"])
            system_prompt = (
                "You are Elira coder agent. Return JSON only with keys: answer, plan, target_path, updated_content, notes. "
                "updated_content must contain the full replacement file content for target_path. "
                "Do not return markdown."
            )
            result = _call_ollama_json(model, system_prompt, _build_code_prompt(payload.message, target_attachment["project_path"], target_attachment["text"], refs))
            response = {
                "status": "ok",
                "session_id": session_id,
                "model": model,
                "route": route,
                "answer": str(result.get("answer", "")),
                "plan": result.get("plan") if isinstance(result.get("plan"), list) else [],
                "attachment_summaries": [_attachment_summary(x) for x in attachments],
                "selected_project_paths": payload.selected_project_paths,
                "web_results": web_results,
                "agents_used": ["coder_agent", "reflection_v2"],
                "code_suggestion": {
                    "target_path": str(result.get("target_path") or target_attachment["project_path"]),
                    "updated_content": str(result.get("updated_content") or ""),
                    "notes": str(result.get("notes") or ""),
                },
            }
            session["messages"].append({"role": "user", "content": payload.message, "ts": time.time()})
            session["messages"].append({"role": "assistant", "content": response["answer"], "ts": time.time(), "route": route})
            return response

    system_prompt = (
        "You are Elira chat-first local agent. Return JSON only with keys: answer, plan, sources_note, suggested_agent, image_prompt. "
        "For plan mode, plan should be a list of short steps. For image requests, answer briefly and fill image_prompt."
    )
    result = _call_ollama_json(model, system_prompt, _build_chat_prompt(payload.message, route["mode"], attachments, project_refs, web_results))
    answer = str(result.get("answer") or "")
    plan = result.get("plan") if isinstance(result.get("plan"), list) else []
    if not answer:
        answer = "Не удалось получить содержательный ответ от модели."

    response = {
        "status": "ok",
        "session_id": session_id,
        "model": model,
        "route": route,
        "answer": answer,
        "plan": plan,
        "sources_note": str(result.get("sources_note") or ""),
        "suggested_agent": str(result.get("suggested_agent") or ""),
        "image_prompt": str(result.get("image_prompt") or ""),
        "attachment_summaries": [_attachment_summary(x) for x in attachments],
        "selected_project_paths": payload.selected_project_paths,
        "web_results": web_results,
        "agents_used": [
            "planner_agent" if route["mode"] == "plan" else "chat_agent",
            "browser_agent" if web_results else None,
        ],
    }
    response["agents_used"] = [x for x in response["agents_used"] if x]

    session["messages"].append({"role": "user", "content": payload.message, "ts": time.time()})
    session["messages"].append({"role": "assistant", "content": answer, "ts": time.time(), "route": route})
    return response


@router.post("/agent/ollama/plan")
def ollama_agent_plan(payload: LocalAgentPlanRequest):
    tags = _fetch_ollama_tags()
    model = _pick_model(payload.model, tags)
    system_prompt = "Return JSON only with keys: summary, steps, risks, selected_path. steps must be a list of short strings."
    user_prompt = (
        f"TASK:\n{payload.goal}\n\nFILE:\n{payload.selected_path}\n\nCONTENT:\n{payload.selected_content[:30000]}"
    )
    result = _call_ollama_json(model, system_prompt, user_prompt)
    return {
        "status": "ok",
        "provider": "ollama",
        "model": model,
        "summary": str(result.get("summary") or ""),
        "steps": result.get("steps") if isinstance(result.get("steps"), list) else [],
        "risks": result.get("risks") if isinstance(result.get("risks"), list) else [],
        "selected_path": payload.selected_path,
    }


@router.post("/agent/ollama/run")
def ollama_agent_run(payload: LocalAgentRunRequest):
    tags = _fetch_ollama_tags()
    model = _pick_model(payload.model, tags)
    refs = _read_reference_context(payload.project_files, selected_path=payload.selected_path)
    system_prompt = (
        "You are Elira local coder agent. Return JSON only with keys: answer, plan, target_path, updated_content, notes. "
        "updated_content must be the full file content."
    )
    result = _call_ollama_json(model, system_prompt, _build_code_prompt(payload.goal, payload.selected_path, payload.selected_content, refs))
    return {
        "status": "ok",
        "provider": "ollama",
        "model": model,
        "mode": payload.mode,
        "answer": str(result.get("answer") or ""),
        "plan": result.get("plan") if isinstance(result.get("plan"), list) else [],
        "target_path": str(result.get("target_path") or payload.selected_path),
        "updated_content": str(result.get("updated_content") or ""),
        "notes": str(result.get("notes") or ""),
        "references_used": [item["path"] for item in refs],
    }
