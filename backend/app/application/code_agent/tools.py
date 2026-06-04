"""Code-agent tools — sandboxed file & shell operations exposed to the LLM
via Ollama function-calling.

Every tool receives a `project_root` (resolved Path) and refuses to touch any
path that escapes it. This is the minimal viable tool set for a Claude
Code / Codex-class local agent:
    read_file, write_file, edit_file, glob, grep, run_bash

Each tool returns a structured dict with at minimum:
    {"text": <human-readable summary, fed back to the LLM as tool result>}
Some tools add extra fields the frontend uses to render diffs and
auto-open files (touched_path, old_content, new_content).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from app.application.projects.scope import project_scope_id


_SHELL_TIMEOUT_MAX = 120
_SHELL_STDOUT_LIMIT = 16000
_SHELL_STDERR_LIMIT = 6000
_BLOCKED_SHELL_FRAGMENTS = (
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "format c:",
    "shutdown",
    "reboot",
    ":(){:|:&};:",
    "deltree",
    "remove-item -recurse",
    "del /s",
    "rd /s",
    "rmdir /s",
    "git reset --hard",
    "git clean -fd",
    "git checkout --",
)


# Read-only command prefixes that auto-execute without user approval.
# A command is safe if it starts with one of these prefixes (case-insensitive).
# Anything not in this list and not in _BLOCKED_SHELL_FRAGMENTS requires approval.
_SHELL_READONLY_PREFIXES: tuple[str, ...] = (
    # VCS read-only
    "git status", "git log", "git diff", "git show", "git branch",
    "git remote", "git stash list", "git tag", "git fetch --dry-run",
    "git ls-files", "git describe", "git rev-parse",
    # File listing / reading
    "ls", "dir", "find ", "tree",
    "cat ", "head ", "tail ", "less ", "more ", "type ",
    "wc ", "file ",
    # Search
    "grep ", "egrep ", "fgrep ", "rg ", "ag ",
    # System info / status
    "echo ", "pwd", "whoami", "id", "hostname",
    "which ", "where ", "command -v",
    "env", "printenv", "set",
    "ps ", "ps aux", "top -bn1",
    "df ", "du -sh", "free ",
    # Python / package status
    "python --version", "python3 --version", "python -V", "python3 -V",
    "pip list", "pip show ", "pip freeze", "pip check",
    "uv list", "poetry show",
    # Testing — collect only
    "pytest --collect-only", "pytest -v --collect-only",
    "jest --listTests", "cargo test -- --list",
    # Node / npm status
    "node --version", "npm list", "yarn list", "pnpm list",
    "npm outdated", "npm audit",
    # Docker status
    "docker ps", "docker images", "docker stats", "docker info",
    "docker compose ps", "docker-compose ps",
    # Rust / Go / etc.
    "cargo --version", "rustc --version", "go version",
    # Network / DNS read-only
    "nslookup ", "dig ", "host ", "ping ",
)


def is_shell_safe(command: str) -> bool:
    """Return True if *command* is in the read-only shell allowlist.

    Commands in this set auto-execute without user approval. Matching is
    prefix-based and case-insensitive so ``git status --short`` passes as
    a ``git status`` prefix.

    Prefixes that end with a space (e.g. ``"cat "``) match any command that
    starts with that string (``cat README.md``). Prefixes without a trailing
    space (e.g. ``"git status"``) are matched as exact or word-boundary
    (``git status``, ``git status --short``).
    """
    cmd = (command or "").strip().lower()
    if not cmd:
        return False
    # Reject any command containing shell composition or redirection metacharacters.
    # These could chain an unsafe subcommand past the prefix check.
    _UNSAFE_METACHAR = ("&&", "||", ";;", "|", ";", ">", "<", "`", "$(", "\n", "\r")
    if any(meta in cmd for meta in _UNSAFE_METACHAR):
        return False
    for prefix in _SHELL_READONLY_PREFIXES:
        p = prefix.lower()
        if p.endswith(" "):
            if cmd.startswith(p) or cmd == p.rstrip():
                return True
        else:
            if cmd == p or cmd.startswith(p + " ") or cmd.startswith(p + "\t"):
                return True
    return False


class SandboxError(Exception):
    """Raised when a tool tries to access a path outside the project root."""


def _resolve_safe(project_root: Path, raw_path: str) -> Path:
    """Resolve `raw_path` (absolute or relative to project_root) and confirm
    it stays inside project_root. Raises SandboxError otherwise.
    """
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    resolved = candidate.resolve()
    root_resolved = project_root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SandboxError(
            f"Path '{raw_path}' resolves to {resolved}, which is outside the "
            f"project root {root_resolved}"
        ) from exc
    return resolved


def _truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    budget = max(400, limit - 100)
    head_size = int(budget * 0.65)
    tail_size = budget - head_size
    removed = len(text) - head_size - tail_size
    return (
        text[:head_size]
        + f"\n[... truncated {removed} chars from middle ...]\n"
        + text[-tail_size:]
    )


def _blocked_shell_fragment(command: str) -> str | None:
    lowered = (command or "").strip().lower()
    return next((fragment for fragment in _BLOCKED_SHELL_FRAGMENTS if fragment in lowered), None)


# ─── tool implementations ────────────────────────────────────────────────────


def tool_read_file(
    project_root: Path,
    *,
    path: str,
    offset: int = 0,
    limit: int = 2000,
) -> dict[str, Any]:
    target = _resolve_safe(project_root, path)
    if not target.is_file():
        return {"text": f"ERROR: not a file or does not exist: {path}"}
    try:
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception as exc:
        return {"text": f"ERROR: {exc}"}
    start = max(0, int(offset))
    end = start + max(1, int(limit))
    selected = lines[start:end]
    numbered = "".join(f"{i + 1 + start:>5}\t{ln}" for i, ln in enumerate(selected))
    suffix = "" if end >= len(lines) else f"\n[... truncated at line {end} of {len(lines)}]"
    return {
        "text": numbered + suffix,
        "touched_path": path,
    }


def tool_write_file(project_root: Path, *, path: str, content: str) -> dict[str, Any]:
    target = _resolve_safe(project_root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    old_content = ""
    if existed:
        try:
            old_content = target.read_text(encoding="utf-8")
        except Exception:
            old_content = ""
    target.write_text(content, encoding="utf-8")
    action = "Overwrote" if existed else "Created"
    return {
        "text": f"{action} {path} ({len(content)} chars)",
        "touched_path": path,
        "old_content": old_content,
        "new_content": content,
        "diff_action": "overwrite" if existed else "create",
    }


def tool_edit_file(
    project_root: Path,
    *,
    path: str,
    old_string: str,
    new_string: str,
) -> dict[str, Any]:
    target = _resolve_safe(project_root, path)
    if not target.is_file():
        return {"text": f"ERROR: not a file or does not exist: {path}"}
    current = target.read_text(encoding="utf-8")
    if old_string not in current:
        return {"text": f"ERROR: old_string not found in {path}"}
    occurrences = current.count(old_string)
    if occurrences > 1:
        return {
            "text": (
                f"ERROR: old_string matches {occurrences} times in {path}. "
                "Provide a larger surrounding context to make it unique."
            )
        }
    updated = current.replace(old_string, new_string, 1)
    target.write_text(updated, encoding="utf-8")
    return {
        "text": f"Edited {path} (1 replacement)",
        "touched_path": path,
        "old_content": current,
        "new_content": updated,
        "diff_action": "edit",
    }


def tool_glob(project_root: Path, *, pattern: str) -> dict[str, Any]:
    root = project_root.resolve()
    matches: list[str] = []
    for raw_match in root.glob(pattern):
        try:
            matches.append(str(raw_match.relative_to(root)).replace("\\", "/"))
        except ValueError:
            continue
    matches.sort()
    if not matches:
        return {"text": f"No files match '{pattern}'"}
    return {"text": "\n".join(matches[:200])}


def tool_grep(
    project_root: Path,
    *,
    pattern: str,
    path: str = ".",
    glob: str = "*",
) -> dict[str, Any]:
    base = _resolve_safe(project_root, path)
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return {"text": f"ERROR: invalid regex: {exc}"}
    if base.is_file():
        files = [base]
    else:
        files = [
            f for f in base.rglob(glob)
            if f.is_file() and ".git" not in f.parts and "node_modules" not in f.parts
        ]
    out: list[str] = []
    for f in files:
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if regex.search(line):
                        rel = str(f.relative_to(project_root.resolve())).replace("\\", "/")
                        out.append(f"{rel}:{lineno}:{line.rstrip()}")
                        if len(out) >= 200:
                            break
        except Exception:
            continue
        if len(out) >= 200:
            out.append("[... truncated at 200 matches]")
            break
    return {"text": "\n".join(out) if out else f"No matches for '{pattern}' in {path}"}


def tool_recall(
    project_root: Path,
    *,
    query: str,
    top_k: int = 5,
    min_score: float = 0.3,
) -> dict[str, Any]:
    """Semantic search over RAG memory. Returns top matching items —
    relevant code chunks (if the project was indexed) and summaries of
    prior agent turns.

    Scope: results are restricted to entries tagged with this project
    name, plus global entries (project='') so user-level facts still
    surface. Cross-project leakage is prevented.
    """
    try:
        from app.application.rag_memory.service import search_rag
    except Exception as exc:
        return {"text": f"ERROR: RAG service unavailable: {exc}"}

    scope_id = project_scope_id(project_root)
    result = search_rag(
        query=query,
        limit=max(1, int(top_k)),
        min_score=float(min_score),
        project=scope_id,
    )
    if not result.get("ok"):
        return {"text": f"ERROR: {result.get('error', 'recall failed')}"}
    items = result.get("items", []) or []
    if not items:
        return {"text": f"No matches for '{query}' (min_score={min_score})"}
    lines = [f"Found {len(items)} relevant items:"]
    for i, item in enumerate(items, 1):
        score = item.get("score", 0.0)
        category = item.get("category", "fact")
        text = (item.get("text") or "").strip()
        if len(text) > 600:
            text = text[:600] + " [...]"
        lines.append(f"\n[{i}] score={score:.2f}  category={category}\n{text}")
    return {"text": "\n".join(lines)}


def tool_web_search(*, query: str, top_k: int = 5) -> dict[str, Any]:
    """Search the web via the configured engines (Tavily / DuckDuckGo /
    Wikipedia). Returns ranked results with title + URL + snippet. Use
    `web_fetch` after this to read the full content of a specific result.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return {"text": "ERROR: query is empty"}
    try:
        from app.infrastructure.search.web_search import search_web
    except Exception as exc:  # pragma: no cover - import path
        return {"text": f"ERROR: web search unavailable: {exc}"}

    limit = max(1, min(int(top_k), 10))
    result = search_web(cleaned, max_results=limit)
    sources = result.get("sources") or []
    if not sources:
        return {"text": f"No web results for '{cleaned}'"}

    engines = ", ".join(result.get("engines_used") or []) or "?"
    lines = [f"Found {len(sources)} results via {engines}:"]
    for i, item in enumerate(sources[:limit], 1):
        title = (item.get("title") or "").strip() or "(no title)"
        url = (item.get("url") or "").strip()
        snippet = (item.get("snippet") or item.get("content") or "").strip()
        if len(snippet) > 350:
            snippet = snippet[:350] + " […]"
        lines.append(f"\n[{i}] {title}\n    {url}\n    {snippet}" if snippet else f"\n[{i}] {title}\n    {url}")
    return {"text": "\n".join(lines)}


def tool_sandbox_run(
    project_root: Path,
    *,
    code: str,
    install: list[str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Execute Python code in an isolated per-project venv.

    Persistent: pip installs and any files the script writes to
    `./work/` survive between calls within the same project.
    Reset with `sandbox_reset`.
    """
    if not isinstance(code, str) or not code.strip():
        return {"text": "ERROR: code is empty"}
    from app.application.code_agent.sandbox import run_in_sandbox

    result = run_in_sandbox(
        project_root,
        code=code,
        install=install,
        timeout=int(timeout),
    )

    parts: list[str] = []
    parts.append(f"[sandbox: {result['sandbox_path']}]")
    parts.append(f"exit={result['exit_code']}  took={result['took_seconds']}s")
    if result.get("error"):
        parts.append(f"ERROR: {result['error']}")
    if result.get("install_log"):
        parts.append(f"PIP:\n{result['install_log']}")
    if result.get("stdout"):
        parts.append(f"STDOUT:\n{result['stdout']}")
    if result.get("stderr"):
        parts.append(f"STDERR:\n{result['stderr']}")
    return {"text": "\n".join(parts)}


def tool_sandbox_reset(project_root: Path) -> dict[str, Any]:
    """Wipe the project's sandbox (venv + work dir). Next sandbox_run
    starts fresh."""
    from app.application.code_agent.sandbox import reset_sandbox

    result = reset_sandbox(project_root)
    if not result.get("ok"):
        return {"text": f"ERROR: {result.get('error', 'reset failed')}"}
    if not result.get("existed"):
        return {"text": "Sandbox did not exist (nothing to reset)."}
    return {"text": f"Sandbox reset: {result['sandbox_path']}"}


def tool_web_fetch(*, url: str, max_chars: int = 8000) -> dict[str, Any]:
    """Fetch a single web page and extract its main readable text.

    HTML noise (nav, footer, ads, scripts) is stripped via the project's
    existing BeautifulSoup-based extractor. Use this AFTER `web_search`
    has surfaced URLs worth reading in full.
    """
    cleaned_url = (url or "").strip()
    if not cleaned_url:
        return {"text": "ERROR: url is empty"}
    if not (cleaned_url.startswith("http://") or cleaned_url.startswith("https://")):
        return {"text": f"ERROR: url must start with http:// or https:// — got '{cleaned_url[:80]}'"}

    from app.application.web.ssrf_guard import check_ssrf
    ssrf_reason = check_ssrf(cleaned_url)
    if ssrf_reason:
        return {"text": f"ERROR: SSRF blocked — {ssrf_reason}"}

    try:
        from app.infrastructure.search.web_search import fetch_page_text
    except Exception as exc:  # pragma: no cover
        return {"text": f"ERROR: web fetch unavailable: {exc}"}

    limit = max(500, min(int(max_chars), 50000))
    try:
        body = fetch_page_text(cleaned_url, max_chars=limit)
    except Exception as exc:
        return {"text": f"ERROR: {exc}"}

    body = (body or "").strip()
    if not body:
        return {"text": f"ERROR: empty or non-HTML response from {cleaned_url}"}
    return {"text": f"[fetched: {cleaned_url}]\n\n{body}"}


def tool_run_bash(project_root: Path, *, command: str, timeout: int = 60) -> dict[str, Any]:
    cleaned_command = (command or "").strip()
    if not cleaned_command:
        return {"text": "ERROR: command is empty"}
    blocked = _blocked_shell_fragment(cleaned_command)
    if blocked:
        return {"text": f"ERROR: blocked dangerous shell command fragment: {blocked}"}
    safe_timeout = max(1, min(int(timeout), _SHELL_TIMEOUT_MAX))
    try:
        proc = subprocess.run(
            cleaned_command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=safe_timeout,
            cwd=str(project_root.resolve()),
        )
    except subprocess.TimeoutExpired:
        return {"text": f"ERROR: command timed out after {safe_timeout}s"}
    except Exception as exc:
        return {"text": f"ERROR: {exc}"}
    parts = [f"$ {cleaned_command}", f"exit={proc.returncode}"]
    if proc.stdout:
        parts.append(f"STDOUT:\n{_truncate_middle(proc.stdout.rstrip(), _SHELL_STDOUT_LIMIT)}")
    if proc.stderr:
        parts.append(f"STDERR:\n{_truncate_middle(proc.stderr.rstrip(), _SHELL_STDERR_LIMIT)}")
    return {"text": "\n".join(parts)}


# ─── P10.1: deferred tool search meta-tool (foundation) ─────────────────────

TOOL_SEARCH_RESULT_LIMIT = 20
TOOL_SEARCH_ACTIVATION_CAP = 5


def _clamp_to_max(value: Any, maximum: int) -> int:
    """Coerce a caller-controlled int and clamp to [0, maximum]; non-int / bad
    values fall back to `maximum`. Guarantees the model can never exceed the cap."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = maximum
    return min(max(0, n), maximum)


def _record_tool_search_metrics(
    run_id: str, query: str, match_count: int, activated: list[str], agent_id: str
) -> None:
    """Best-effort run metrics for tool.search / tool.activated (no schema change)."""
    try:
        from app.application.monitoring.runtime import record_metric

        record_metric(
            metric_type="tool.search",
            agent_id=agent_id,
            run_id=run_id,
            ok=True,
            details={
                "query": str(query),
                "match_count": int(match_count),
                "activated_count": len(activated),
            },
        )
        if activated:
            record_metric(
                metric_type="tool.activated",
                agent_id=agent_id,
                run_id=run_id,
                ok=True,
                details={"tools": list(activated), "query": str(query)},
            )
    except Exception:
        pass


def tool_search(
    *,
    run_id: str,
    query: str,
    agent_id: str = "code-agent",
    limit: int = TOOL_SEARCH_RESULT_LIMIT,
    activation_cap: int = TOOL_SEARCH_ACTIVATION_CAP,
) -> dict[str, Any]:
    """Read-only meta-tool (P10.1 foundation): search the ToolSpec registry and
    activate eligible, non-side-effect tools for THIS run only.

    - Requires a run_id (activation is run-scoped).
    - Activation grants VISIBILITY only — the unified executor still enforces
      policy / scope / approval at dispatch. This function executes nothing.
    - Disabled / unclassified / forbidden tools are surfaced but never activated.
    - Side-effect tools are surfaced but NOT auto-activated in this slice.
    - Activates at most ``activation_cap`` tools per call.
    - Uses the existing run-scoped deferred_tools store; ``activate_tools`` is a
      no-op unless the run already opted into deferred mode, so a non-deferred
      run is unchanged. No hidden global state.
    """
    rid = str(run_id or "").strip()
    if not rid:
        return {
            "ok": False,
            "text": "tool_search requires a run_id.",
            "error": "run_id_required",
            "matches": [],
            "activated": [],
        }

    from app.application.tool_registry.runtime import search_tool_specs
    from app.application.agent_kernel.deferred_tools import activate_tools

    safe_limit = _clamp_to_max(limit, TOOL_SEARCH_RESULT_LIMIT)
    matches = search_tool_specs(query, limit=safe_limit)

    cap = _clamp_to_max(activation_cap, TOOL_SEARCH_ACTIVATION_CAP)
    eligible: list[str] = []
    for match in matches:
        if len(eligible) >= cap:
            break
        if match["activatable"] and not match["side_effect"]:
            eligible.append(match["name"])

    activated: list[str] = []
    if eligible:
        active_set = activate_tools(rid, eligible)  # no-op unless run is deferred
        activated = [name for name in eligible if name in active_set]

    _record_tool_search_metrics(rid, query, len(matches), activated, agent_id)

    lines = [f"tool_search({query!r}): {len(matches)} match(es), {len(activated)} activated."]
    for match in matches:
        if match["name"] in activated:
            mark = "[activated]"
        elif not match["activatable"]:
            mark = f"[blocked: {match['reason']}]"
        elif match["side_effect"]:
            mark = "[side_effect: not auto-activated]"
        else:
            mark = "[eligible]"
        lines.append(f"  {match['name']} ({match['category']}/{match['source']}) {mark}")

    return {
        "ok": True,
        "text": "\n".join(lines),
        "matches": matches,
        "activated": activated,
    }


# ─── tool registry exposed to Ollama ────────────────────────────────────────


def build_tool_schemas() -> list[dict[str, Any]]:
    """Ollama function-calling tool schemas."""
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from the project. Returns lines with line numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path relative to project root, or absolute inside it."},
                        "offset": {"type": "integer", "description": "Starting line (0-based). Default 0."},
                        "limit": {"type": "integer", "description": "Max lines to read. Default 2000."},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Create a new file or overwrite an existing one with the given content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Replace the single occurrence of `old_string` with `new_string` in `path`. Errors if old_string is not unique.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "glob",
                "description": "Find files matching a glob pattern (e.g. '**/*.py').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": "Search file contents for a regex pattern. Returns 'file:line:match' lines.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string", "description": "Directory or file to search. Default: project root."},
                        "glob": {"type": "string", "description": "Glob filter for files. Default: '*'"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "recall",
                "description": (
                    "Semantic search over the agent's RAG memory. Returns "
                    "relevant code chunks (if the project was indexed) and "
                    "summaries of prior agent runs. Use this before grep when "
                    "looking for 'where is X implemented' or 'what did I do "
                    "last time about Y'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Natural-language search query."},
                        "top_k": {"type": "integer", "description": "Max results (default 5)."},
                        "min_score": {"type": "number", "description": "Cosine similarity threshold 0..1 (default 0.3)."},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_bash",
                "description": "Run a shell command inside the project root. Returns stdout, stderr, and exit code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "description": "Seconds. Default 60."},
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Search the web for current information. Returns ranked "
                    "list of {title, url, snippet}. Use this BEFORE answering "
                    "any question that depends on facts you don't already "
                    "know — current events, library versions, niche docs. "
                    "Call `web_fetch` after on URLs that look relevant."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "top_k": {"type": "integer", "description": "Max results (default 5, max 10)."},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": (
                    "Fetch one URL and extract the main readable text "
                    "(navigation, ads, scripts stripped). Use AFTER "
                    "`web_search` to actually read a page, not just see "
                    "its snippet. Output is plain text up to max_chars."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full http(s) URL."},
                        "max_chars": {"type": "integer", "description": "Truncate body to this many chars (default 8000, max 50000)."},
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sandbox_run",
                "description": (
                    "Execute Python code in an isolated per-project venv. "
                    "Use this (NOT run_bash) for: experimenting with a "
                    "library, prototyping a snippet, anything that needs "
                    "`pip install` of packages you don't want in the user's "
                    "main environment. The sandbox PERSISTS between calls — "
                    "installed packages and files in ./work/ stay. cwd is "
                    "the work/ directory; the user's project tree is NOT "
                    "touched."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python source to execute."},
                        "install": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of pip package specs to install before running (e.g. ['requests', 'rich>=13']).",
                        },
                        "timeout": {"type": "integer", "description": "Seconds. Default 60."},
                    },
                    "required": ["code"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sandbox_reset",
                "description": (
                    "Wipe the project's sandbox: removes the venv AND "
                    "everything in work/. Use when the sandbox has gotten "
                    "into a broken state or you want a clean slate. Next "
                    "sandbox_run rebuilds from scratch (~3-5s for the venv)."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


def build_tool_dispatch(project_root: Path) -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "read_file": lambda **kw: tool_read_file(project_root, **kw),
        "write_file": lambda **kw: tool_write_file(project_root, **kw),
        "edit_file": lambda **kw: tool_edit_file(project_root, **kw),
        "glob": lambda **kw: tool_glob(project_root, **kw),
        "grep": lambda **kw: tool_grep(project_root, **kw),
        "recall": lambda **kw: tool_recall(project_root, **kw),
        "run_bash": lambda **kw: tool_run_bash(project_root, **kw),
        "web_search": lambda **kw: tool_web_search(**kw),
        "web_fetch": lambda **kw: tool_web_fetch(**kw),
        "sandbox_run": lambda **kw: tool_sandbox_run(project_root, **kw),
        "sandbox_reset": lambda **kw: tool_sandbox_reset(project_root, **kw),
    }
