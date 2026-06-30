"""HTTP entrypoint for the tool-using code agent.

Three endpoints:
  POST /api/code-agent/run       — legacy single-shot (drain stream, return dict)
  POST /api/code-agent/stream    — SSE: yields run_started / step_started /
                                   tool_call / final_response / done events
  POST /api/code-agent/cancel    — flip the cancel flag for a running run_id
  GET  /api/code-agent/project-prompt?project_root=...  — read .elira/agent.md
  PUT  /api/code-agent/project-prompt                   — write it
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Literal, Optional
from urllib.parse import urlparse, urlunparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.application.code_agent.agent_loop import (
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL,
    DEFAULT_NUM_CTX,
    _CODE_AGENT_BASE_TOOLS,
    get_project_prompt,
    init_project_prompt,
    index_project,
    recall_from_rag,
    request_cancel,
    run_code_agent,
    set_project_prompt,
    stream_code_agent,
    summarize_history,
)
from app.application.code_agent import sessions as session_store
from app.application.chat.local_chat import resolve_persona_mode
from app.application.library.runtime import build_library_context
from app.core.data_files import data_subdir

router = APIRouter(prefix="/api/code-agent", tags=["code-agent"])

CodeAgentMode = Literal["code", "search"]
_FAVICON_MAX_BYTES = 128 * 1024


def _base_tools_for_mode(mode: CodeAgentMode) -> tuple[str, ...] | None:
    if mode == "search":
        return tuple(dict.fromkeys((*_CODE_AGENT_BASE_TOOLS, "web_search", "web_fetch")))
    return None


def _inject_library_context(message: str) -> str:
    """Prepend active library-file previews to the user's message.

    Documents/images dropped into the workspace are stored with
    use_in_context=1 (see uploadLibraryFile). The code agent's tool loop is
    unaware of the library, so the dropped file is invisible unless we surface
    it here — mirroring how the chat path injects library context. The block is
    bounded (build_library_context caps files/chars) so it cannot blow the
    context window.
    """
    try:
        ctx = build_library_context()
    except Exception:
        return message
    block = (ctx.get("context") or "").strip()
    if not block:
        return message
    used = ", ".join(ctx.get("used_files") or []) or "вложения"
    header = (
        "Контекст из прикреплённых файлов "
        f"({used}). Используй его, если он относится к запросу:"
    )
    return f"{header}\n\n{block}\n\n----- ЗАПРОС ПОЛЬЗОВАТЕЛЯ -----\n{message}"


class CodeAgentAttachment(BaseModel):
    ok: bool = True
    filename: str = ""
    kind: str = "document"
    text: str = ""
    chars: int = 0
    note: Optional[str] = None


def _inject_attachment_context(message: str, attachments: list[CodeAgentAttachment] | None) -> str:
    """Fold parsed attachment text into the core user message.

    Mirrors the old chat stream path: OCR/vision/document parsing happens before
    this request, and the code-agent receives only bounded text metadata.
    """
    blocks: list[str] = []
    for attachment in attachments or []:
        text = str(attachment.text or "").strip()
        if not text:
            continue
        kind = str(attachment.kind or "document").strip() or "document"
        filename = str(attachment.filename or "attachment").strip() or "attachment"
        if kind == "audio":
            blocks.append(
                f"[ВЛОЖЕНИЕ — аудиозапись «{filename}», УЖЕ автоматически распознанная в текст "
                f"(сервер сделал speech-to-text). Ниже — готовая расшифровка этой записи. Если "
                f"пользователь просит «расшифруй / прочитай запись / что там» — ответ это и есть: "
                f"приведи этот текст ДОСЛОВНО, слово в слово, без пересказа и без правок (чистить "
                f"повторы/слова-паразиты или сокращать — ТОЛЬКО если попросят явно). НЕ говори, что "
                f"не умеешь работать с аудио, и НЕ проси прислать файл — звук уже распознан.]\n"
                f"РАСШИФРОВКА:\n{text}"
            )
        else:
            blocks.append(f"[{kind}: {filename}]\n{text}")
    if not blocks:
        return message
    attachment_block = "\n\n".join(blocks)
    return f"{message.strip()}\n\n{attachment_block}" if message.strip() else attachment_block


def _resolve_project_root(raw: str | None) -> str:
    """Default an empty/blank project_root to a writable scratch workspace.

    Lets chat work without first picking a folder, and avoids silently falling
    back to the backend's own cwd (which would let the agent touch app files).
    """
    p = (raw or "").strip()
    if p:
        return p
    return str(data_subdir("agent_workspace"))


def _favicon_url_for_source(raw_url: str) -> str:
    parsed = urlparse((raw_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="url must be an absolute http(s) URL")
    return urlunparse((parsed.scheme, parsed.netloc, "/favicon.ico", "", "", ""))


def _favicon_media_type(content_type: str, content: bytes) -> str:
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type.startswith("image/"):
        return media_type
    if content.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return ""


class ConversationMessage(BaseModel):
    role: str = Field(..., description="user or assistant")
    content: str


class CodeAgentRequest(BaseModel):
    message: str = Field(..., description="User task for the code agent")
    project_root: str = Field(..., description="Absolute path to the project directory")
    working_dir: Optional[str] = Field(default=None, description="Optional subdirectory inside project_root")
    # P9.3: "auto" routes through the shared model order (route='code') server-side;
    # an explicit model is preserved. (CodeAgentStreamRequest inherits this.)
    model: str = Field(default="auto")
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1, le=100)
    num_ctx: int = Field(default=DEFAULT_NUM_CTX, ge=1024, le=131072)
    mode: CodeAgentMode = Field(default="code", description="Composer mode: code or search")
    auto_remember: bool = Field(default=True, description="Save a short summary of successful turns into RAG")
    conversation_history: list[ConversationMessage] | None = None
    attachments: list[CodeAgentAttachment] | None = None
    access_mode: Literal["project-workspace"] = Field(
        default="project-workspace",
        description="Enforced code-agent access profile; broader profiles are not enabled.",
    )
    profile_name: str = Field(
        default="Авто",
        description="Persona mode: Авто (Elira picks per message), Личный, Баланс or Инженерный. Legacy profile names are migrated automatically.",
    )


class CodeAgentResponse(BaseModel):
    ok: bool
    response: str
    steps: int
    tool_calls: list
    stop_reason: str
    error: Optional[str] = None
    partial: bool = False


class CodeAgentStreamRequest(CodeAgentRequest):
    run_id: Optional[str] = Field(default=None, description="Client-provided ID; needed if you want to /cancel later")
    approval_wait_seconds: int = Field(
        default=300, ge=0, le=3600,
        description="How long the loop pauses waiting for a human approval (0 = legacy no-pause)",
    )


class CodeAgentCancelRequest(BaseModel):
    run_id: str


class ProjectPromptWriteRequest(BaseModel):
    project_root: str
    content: str


class ProjectPromptInitRequest(BaseModel):
    project_root: str
    content: Optional[str] = None


class SummarizeHistoryRequest(BaseModel):
    messages: list[ConversationMessage]
    model: str = Field(default=DEFAULT_MODEL)
    num_ctx: int = Field(default=DEFAULT_NUM_CTX, ge=1024, le=131072)


class SummarizeHistoryResponse(BaseModel):
    ok: bool
    summary: str
    turn_count: int
    error: Optional[str] = None


class IndexProjectRequest(BaseModel):
    project_root: str
    patterns: Optional[list[str]] = None
    replace: bool = True


class RecallRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=50)
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)
    project_root: Optional[str] = Field(default=None, description="Optional project path for scoped recall")


@router.post("/run", response_model=CodeAgentResponse)
def run(payload: CodeAgentRequest) -> CodeAgentResponse:
    history = [m.model_dump() for m in (payload.conversation_history or [])]
    user_message = _inject_library_context(_inject_attachment_context(payload.message, payload.attachments))
    result = run_code_agent(
        user_message=user_message,
        project_root=_resolve_project_root(payload.project_root),
        working_dir=payload.working_dir,
        model=payload.model,
        max_steps=payload.max_steps,
        conversation_history=history,
        num_ctx=payload.num_ctx,
        base_tools=_base_tools_for_mode(payload.mode),
        auto_remember=payload.auto_remember,
        access_mode=payload.access_mode,
        profile_name=resolve_persona_mode(payload.profile_name, user_message),
    )
    return CodeAgentResponse(**result)


def _sse_format(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/stream")
def stream(payload: CodeAgentStreamRequest) -> StreamingResponse:
    run_id = payload.run_id or uuid.uuid4().hex
    history = [m.model_dump() for m in (payload.conversation_history or [])]
    user_message = _inject_library_context(_inject_attachment_context(payload.message, payload.attachments))

    def gen():
        try:
            for event in stream_code_agent(
                user_message=user_message,
                project_root=_resolve_project_root(payload.project_root),
                working_dir=payload.working_dir,
                model=payload.model,
                max_steps=payload.max_steps,
                conversation_history=history,
                num_ctx=payload.num_ctx,
                base_tools=_base_tools_for_mode(payload.mode),
                auto_remember=payload.auto_remember,
                run_id=run_id,
                approval_wait_seconds=payload.approval_wait_seconds,
                access_mode=payload.access_mode,
                profile_name=resolve_persona_mode(payload.profile_name, user_message),
            ):
                yield _sse_format(event)
        except Exception as exc:
            yield _sse_format({
                "type": "done",
                "ok": False,
                "steps": 0,
                "stop_reason": "error",
                "error": str(exc),
            })

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Run-Id": run_id,
        },
    )


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str) -> StreamingResponse:
    """Continue a persisted interrupted/partial run with the same run id."""
    from app.application.code_agent.run_journal import RunJournal

    try:
        journal = RunJournal.load(run_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    state = journal.state
    if not state.get("resumable"):
        raise HTTPException(status_code=409, detail=f"run is not resumable: {run_id}")
    request_data = state.get("request") or {}
    if not isinstance(request_data, dict):
        raise HTTPException(status_code=409, detail=f"run request is invalid: {run_id}")

    history = list(request_data.get("conversation_history") or [])
    original_message = str(request_data.get("user_message") or "").strip()
    if original_message:
        history.append({"role": "user", "content": original_message})
    last_response = str(state.get("last_response") or "").strip()
    if last_response:
        history.append({"role": "assistant", "content": last_response})

    def gen():
        for event in stream_code_agent(
            user_message=(
                "Продолжи незавершённую задачу с последнего подтверждённого результата. "
                "Сначала проверь фактическое состояние файлов и не повторяй уже выполненные изменения."
            ),
            project_root=_resolve_project_root(str(request_data.get("project_root") or "")),
            working_dir=request_data.get("working_dir"),
            model=str(request_data.get("model") or "auto"),
            agent_id=str(request_data.get("agent_id") or "code-agent"),
            max_steps=int(request_data.get("max_steps") or DEFAULT_MAX_STEPS),
            conversation_history=history,
            run_id=run_id,
            num_ctx=int(request_data.get("num_ctx") or DEFAULT_NUM_CTX),
            base_tools=tuple(request_data.get("base_tools") or _CODE_AGENT_BASE_TOOLS),
            execution_timeout_seconds=request_data.get("execution_timeout_seconds"),
            auto_remember=bool(request_data.get("auto_remember", True)),
            approval_wait_seconds=300,
            resume=True,
            access_mode=str(request_data.get("access_mode") or "project-workspace"),
        ):
            yield _sse_format(event)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Run-Id": run_id,
        },
    )


@router.post("/cancel")
def cancel(payload: CodeAgentCancelRequest) -> dict[str, Any]:
    found = request_cancel(payload.run_id)
    return {"ok": True, "found": found, "run_id": payload.run_id}


@router.get("/context-profile")
def read_context_profile(model: str = DEFAULT_MODEL, num_ctx: Optional[int] = None) -> dict[str, Any]:
    """Initial (empty-history) context usage so the composer can show the
    window meter before the first model turn. The same get_context_usage path
    the agent loop emits per-step, seeded with the live ctx_size profile."""
    from app.application.context.profile import get_active_context_profile
    from app.application.context.usage import get_context_usage

    ctx_size = num_ctx if (num_ctx and num_ctx > 0) else None
    profile = get_active_context_profile(model, ctx_size=ctx_size)
    usage = get_context_usage(
        [],
        ctx_size=int(profile["ctx_size"]),
        reserved_output_tokens=int(profile["reserved_output_tokens"]),
        reserved_system_tokens=int(profile["reserved_system_tokens"]),
        safety_margin_tokens=int(profile["safety_margin_tokens"]),
    )
    return {"ok": True, "context": usage, "source": profile.get("source")}


@router.get("/favicon")
def favicon(url: str) -> Response:
    favicon_url = _favicon_url_for_source(url)
    from app.application.web.ssrf_guard import check_ssrf
    ssrf_reason = check_ssrf(favicon_url)
    if ssrf_reason:
        raise HTTPException(status_code=400, detail=f"SSRF blocked: {ssrf_reason}")

    try:
        import requests
        with requests.get(
            favicon_url,
            headers={"User-Agent": "EliraAI/1.0"},
            timeout=3,
            allow_redirects=False,
            stream=True,
        ) as resp:
            if resp.status_code != 200:
                return Response(status_code=204)
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > _FAVICON_MAX_BYTES:
                    return Response(status_code=204)
                chunks.append(chunk)
            content = b"".join(chunks)
            media_type = _favicon_media_type(resp.headers.get("content-type", ""), content)
            if not content or not media_type:
                return Response(status_code=204)
            return Response(
                content=content,
                media_type=media_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except Exception:
        return Response(status_code=204)


@router.get("/project-prompt")
def read_project_prompt(project_root: str) -> dict[str, Any]:
    if not project_root:
        raise HTTPException(status_code=400, detail="project_root is required")
    return get_project_prompt(project_root)


@router.put("/project-prompt")
def write_project_prompt(payload: ProjectPromptWriteRequest) -> dict[str, Any]:
    result = set_project_prompt(payload.project_root, payload.content)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "failed to write"))
    return result


@router.post("/project-prompt/init")
def init_project_prompt_endpoint(payload: ProjectPromptInitRequest) -> dict[str, Any]:
    result = init_project_prompt(payload.project_root, payload.content)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "failed to initialize"))
    return result


@router.post("/summarize-history", response_model=SummarizeHistoryResponse)
def summarize(payload: SummarizeHistoryRequest) -> SummarizeHistoryResponse:
    messages = [m.model_dump() for m in payload.messages]
    result = summarize_history(messages=messages, model=payload.model, num_ctx=payload.num_ctx)
    return SummarizeHistoryResponse(**result)


@router.post("/index-project")
def index_project_endpoint(payload: IndexProjectRequest) -> dict[str, Any]:
    result = index_project(
        project_root=payload.project_root,
        patterns=payload.patterns,
        replace=payload.replace,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "indexing failed"))
    return result


@router.post("/recall")
def recall(payload: RecallRequest) -> dict[str, Any]:
    return recall_from_rag(
        query=payload.query,
        top_k=payload.top_k,
        min_score=payload.min_score,
        project_root=payload.project_root,
    )


# ── File watcher (realtime auto-reindex on edits) ────────────────────────

class WatcherRequest(BaseModel):
    project_root: str


@router.post("/watcher/start")
def watcher_start(payload: WatcherRequest) -> dict[str, Any]:
    from app.application.code_agent.file_watcher import start_watcher
    result = start_watcher(payload.project_root)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "watcher start failed"))
    return result


@router.post("/watcher/stop")
def watcher_stop(payload: WatcherRequest) -> dict[str, Any]:
    from app.application.code_agent.file_watcher import stop_watcher
    return stop_watcher(payload.project_root)


@router.get("/watcher/status")
def watcher_status_endpoint(project_root: Optional[str] = None) -> dict[str, Any]:
    from app.application.code_agent.file_watcher import watcher_status
    return watcher_status(project_root)


# ── SSH allowlist (the security boundary for the SshToolProvider) ───────

class SshConfigRequest(BaseModel):
    allowed_hosts: list[str]


@router.get("/ssh/config")
def ssh_config_get() -> dict[str, Any]:
    """Return the current SSH allowlist + enabled flag.

    Enabled iff allowed_hosts is non-empty — there's no separate
    toggle. To disable SSH entirely, POST allowed_hosts=[]."""
    from app.application.tool_providers.ssh_acl import get_allowed_hosts, is_ssh_enabled
    return {
        "enabled": is_ssh_enabled(),
        "allowed_hosts": get_allowed_hosts(),
    }


@router.post("/ssh/config")
def ssh_config_set(payload: SshConfigRequest) -> dict[str, Any]:
    """Replace the SSH allowlist atomically. Returns the persisted
    list after normalization (trimmed, deduped, empty entries removed)."""
    from app.application.tool_providers.ssh_acl import set_allowed_hosts, is_ssh_enabled
    persisted = set_allowed_hosts(payload.allowed_hosts)
    return {
        "ok": True,
        "enabled": is_ssh_enabled(),
        "allowed_hosts": persisted,
    }


# ── MCP servers ─────────────────────────────────────────────────────────

class McpServerSpec(BaseModel):
    id: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class McpServersRequest(BaseModel):
    servers: list[McpServerSpec]


class McpServerActionRequest(BaseModel):
    server_id: str


class McpPromptGetRequest(BaseModel):
    server_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    max_chars: int = Field(default=50_000, ge=1, le=200_000)


def _mcp_context_limit(max_chars: int) -> int:
    return max(1, min(int(max_chars or 50_000), 200_000))


def _live_mcp_client_or_404(server_id: str):
    from app.application.tool_providers.mcp_runtime import get_live_client
    client = get_live_client(server_id)
    if client is None:
        raise HTTPException(status_code=404, detail=f"server '{server_id}' is not running")
    return client


@router.get("/mcp/servers")
def mcp_list_servers() -> dict[str, Any]:
    """All configured MCP servers + live status."""
    from app.application.tool_providers.mcp_runtime import list_servers
    return {"servers": list_servers()}


@router.post("/mcp/servers")
def mcp_save_servers(payload: McpServersRequest) -> dict[str, Any]:
    """Replace the MCP server list atomically. Any server whose
    spec changed (or that was removed) is stopped automatically."""
    from app.application.tool_providers.mcp_runtime import save_servers
    persisted = save_servers([s.model_dump() for s in payload.servers])
    return {"ok": True, "servers": persisted}


@router.post("/mcp/start")
def mcp_start(payload: McpServerActionRequest) -> dict[str, Any]:
    from app.application.tool_providers.mcp_runtime import start_server
    result = start_server(payload.server_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "start failed"))
    return result


@router.post("/mcp/stop")
def mcp_stop(payload: McpServerActionRequest) -> dict[str, Any]:
    from app.application.tool_providers.mcp_runtime import stop_server
    return stop_server(payload.server_id)


@router.post("/mcp/restart")
def mcp_restart(payload: McpServerActionRequest) -> dict[str, Any]:
    from app.application.tool_providers.mcp_runtime import restart_server
    result = restart_server(payload.server_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "restart failed"))
    return result


@router.get("/mcp/tools")
def mcp_list_tools(server_id: str) -> dict[str, Any]:
    """Tools exposed by a running MCP server (raw, no namespacing).
    Used by the UI to preview what an MCP install actually offers."""
    client = _live_mcp_client_or_404(server_id)
    try:
        return {"server_id": server_id, "tools": client.list_tools()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── LSP servers (D2) ────────────────────────────────────────────────────
#
# Mirrors the MCP routes above: config lives in data/lsp_servers.json, and
# servers are started lazily by an explicit POST /lsp/start — never in
# main.py — so LSP is disabled-by-default. POST /lsp/stop is the explicit
# shutdown the deferred-track spec requires.

class LspServerSpec(BaseModel):
    id: str
    language: str
    command: str
    args: list[str] = Field(default_factory=list)
    enabled: bool = False


class LspServersRequest(BaseModel):
    servers: list[LspServerSpec]


class LspServerActionRequest(BaseModel):
    server_id: str
    project_root: Optional[str] = None


@router.get("/lsp/servers")
def lsp_list_servers() -> dict[str, Any]:
    """All configured LSP servers + live status."""
    from app.application.tool_providers.lsp_runtime import list_servers
    return {"servers": list_servers()}


@router.post("/lsp/servers")
def lsp_save_servers(payload: LspServersRequest) -> dict[str, Any]:
    """Replace the LSP server list atomically. Any server whose
    spec changed (or that was removed) is stopped automatically."""
    from app.application.tool_providers.lsp_runtime import save_servers
    persisted = save_servers([s.model_dump() for s in payload.servers])
    return {"ok": True, "servers": persisted}


@router.post("/lsp/start")
def lsp_start(payload: LspServerActionRequest) -> dict[str, Any]:
    from app.application.tool_providers.lsp_runtime import start_server
    result = start_server(payload.server_id, payload.project_root)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "start failed"))
    return result


@router.post("/lsp/stop")
def lsp_stop(payload: LspServerActionRequest) -> dict[str, Any]:
    from app.application.tool_providers.lsp_runtime import stop_server
    return stop_server(payload.server_id)


@router.post("/lsp/restart")
def lsp_restart(payload: LspServerActionRequest) -> dict[str, Any]:
    from app.application.tool_providers.lsp_runtime import restart_server
    result = restart_server(payload.server_id, payload.project_root)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "restart failed"))
    return result


# ── Sessions ────────────────────────────────────────────────────────────


@router.get("/mcp/resources")
def mcp_list_resources(server_id: str) -> dict[str, Any]:
    client = _live_mcp_client_or_404(server_id)
    try:
        return {"server_id": server_id, **client.list_resources()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/mcp/resource-templates")
def mcp_list_resource_templates(server_id: str) -> dict[str, Any]:
    client = _live_mcp_client_or_404(server_id)
    try:
        return {"server_id": server_id, **client.list_resource_templates()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/mcp/resource")
def mcp_read_resource(server_id: str, uri: str, max_chars: int = 50_000) -> dict[str, Any]:
    client = _live_mcp_client_or_404(server_id)
    try:
        return {"server_id": server_id, "uri": uri, **client.read_resource(uri, max_chars=_mcp_context_limit(max_chars))}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/mcp/prompts")
def mcp_list_prompts(server_id: str) -> dict[str, Any]:
    client = _live_mcp_client_or_404(server_id)
    try:
        return {"server_id": server_id, **client.list_prompts()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/mcp/prompt")
def mcp_get_prompt(payload: McpPromptGetRequest) -> dict[str, Any]:
    client = _live_mcp_client_or_404(payload.server_id)
    try:
        return {
            "server_id": payload.server_id,
            "name": payload.name,
            **client.get_prompt(
                payload.name,
                payload.arguments,
                max_chars=_mcp_context_limit(payload.max_chars),
            ),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# -- Sessions --------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    title: Optional[str] = None
    project_root: Optional[str] = None
    model: Optional[str] = None
    num_ctx: Optional[int] = None


class SessionPatchRequest(BaseModel):
    title: Optional[str] = None
    project_root: Optional[str] = None
    model: Optional[str] = None
    num_ctx: Optional[int] = None
    pinned: Optional[bool] = None
    turns: Optional[list[Any]] = None
    context_state: Optional[dict[str, Any]] = None
    task_ledger: Optional[list[dict[str, Any]]] = None
    pinned_items: Optional[list[dict[str, Any]]] = None
    compression_events: Optional[list[dict[str, Any]]] = None


@router.get("/sessions")
def list_code_sessions(query: Optional[str] = None) -> dict[str, Any]:
    return {"ok": True, "sessions": session_store.list_sessions(query)}


@router.get("/sessions/{session_id}")
def read_code_session(session_id: str) -> dict[str, Any]:
    sess = session_store.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    return {"ok": True, "session": sess}


@router.get("/sessions/{session_id}/context")
def read_code_session_context(session_id: str) -> dict[str, Any]:
    from app.application.context.task_state import get_task_context_state

    state = get_task_context_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    return {"ok": True, "state": state}


@router.post("/sessions/{session_id}/context/compact")
def compact_code_session_context(session_id: str) -> dict[str, Any]:
    from app.application.context.task_state import compress_now

    try:
        result = compress_now(session_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"session not found: {session_id}",
        ) from None
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("reason") or "compression rejected")
    return result


@router.delete("/sessions/{session_id}/context/pins/{item_id}")
def delete_code_session_pin(session_id: str, item_id: str) -> dict[str, Any]:
    from app.application.context.memory import unpin_item
    from app.application.context.task_state import load_task_context, save_task_context

    state = load_task_context(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    before = list(state.get("pinned_items") or [])
    after = unpin_item(before, item_id)
    if len(after) == len(before):
        raise HTTPException(status_code=404, detail=f"pin not found: {item_id}")
    state["pinned_items"] = after
    save_task_context(session_id, state)
    return {"ok": True, "removed": item_id, "state": state}


@router.post("/sessions")
def create_code_session(payload: SessionCreateRequest) -> dict[str, Any]:
    sess = session_store.create_session(
        title=payload.title or "Новый чат",
        project_root=payload.project_root,
        model=payload.model,
        num_ctx=payload.num_ctx,
    )
    return {"ok": True, "session": sess}


@router.patch("/sessions/{session_id}")
def patch_code_session(session_id: str, payload: SessionPatchRequest) -> dict[str, Any]:
    patch_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "pinned" in payload.model_dump(exclude_unset=False):
        patch_data["pinned"] = payload.pinned
    sess = session_store.update_session(session_id, patch_data)
    if not sess:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    return {"ok": True, "session": sess}


@router.delete("/sessions/{session_id}")
def delete_code_session(session_id: str) -> dict[str, Any]:
    removed = session_store.delete_session(session_id)
    return {"ok": True, "removed": removed}
