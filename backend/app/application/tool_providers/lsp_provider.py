"""LSP ToolProvider — read-only language-server facts for the agent.

Exposes exactly three tools, backed by `LspClient` over stdio:

  * ``lsp_diagnostics``  — errors/warnings the server reports for a file
  * ``lsp_definition``   — go-to-definition for a symbol at a position
  * ``lsp_references``   — find-references for a symbol at a position

Unlike `mcp_provider.py` (one provider per server, dynamic per-server
tool list), there is ONE `LspToolProvider` for every configured language.
Its three schemas are static; the right server is chosen at dispatch time
from the ``language`` argument (falling back to the single live server).
This mirrors `ssh_provider.py`, which also has a fixed static schema set.

Disabled-by-default: with no configured/running server, `is_enabled()` is
False and `get_schemas()` returns ``[]`` — the agent never sees the tools
and pays nothing. Servers come up only via the explicit ``/lsp/start``
route (see `code_agent_routes.py`), never from `main.py`.

Never-raises invariant (ported from `mcp_provider.py`): `dispatch` catches
``LspError`` and every other ``Exception``, returning ``{"text": "ERROR:
..."}`` so a flaky language server can never crash the agent loop — it just
degrades back to grep.

LSP coordinates are **0-based** (line 0 = first line, char 0 = first
column); the schemas say so explicitly because the LLM tends to assume
1-based editor coordinates otherwise.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.application.code_agent.tools import (
    SandboxError,
    _resolve_safe,
    _truncate_middle,
)
from app.application.tool_providers.lsp_client import LspClient, LspError
from app.application.tool_providers.lsp_runtime import (
    _path_to_uri,
    live_clients,
)


logger = logging.getLogger(__name__)


# How many definition/reference locations we hand back before truncating —
# a hallucinating query (or a wildly-common symbol) can return thousands.
_RESULT_LIMIT = 50
# Diagnostics tend to be noisier; cap higher but still bounded.
_DIAG_LIMIT = 100
# Hard ceiling on the rendered text blob handed to the LLM.
_TEXT_LIMIT = 16_000

_TOOL_NAMES = {"lsp_diagnostics", "lsp_definition", "lsp_references"}

# Map a file extension to the LSP languageId used in didOpen. Only a hint
# for the server's own classification; the *server* is still chosen by the
# caller's `language` arg / the single-live fallback, not by this.
_EXT_TO_LANGUAGE_ID = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
}


def _language_id_for(path: Path, fallback: str) -> str:
    return _EXT_TO_LANGUAGE_ID.get(path.suffix.lower(), fallback)


# ── schema ───────────────────────────────────────────────────────


def _schemas() -> list[dict[str, Any]]:
    """The three static read-only schemas. ``[lsp]`` prefix is provenance
    in the agent's tool list. Coordinates documented as 0-based."""
    path_prop = {
        "type": "string",
        "description": "Path to the source file, relative to the project root.",
    }
    language_prop = {
        "type": "string",
        "description": (
            "Optional language id (e.g. 'python', 'typescript') used to pick "
            "the language server when more than one is running. Omit if only "
            "one server is up."
        ),
    }
    line_prop = {
        "type": "integer",
        "description": "0-based line number (line 0 = the first line of the file).",
    }
    char_prop = {
        "type": "integer",
        "description": "0-based character offset within the line (0 = first column).",
    }
    return [
        {
            "type": "function",
            "function": {
                "name": "lsp_diagnostics",
                "description": (
                    "[lsp] Read-only language-server diagnostics (errors, warnings, "
                    "hints) for a single file, from the actual language server rather "
                    "than guessed from text. Reflects the file's current on-disk state."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"path": path_prop, "language": language_prop},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lsp_definition",
                "description": (
                    "[lsp] Read-only go-to-definition: where the symbol at the given "
                    "0-based position is defined. Returns file locations, never edits."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": path_prop,
                        "line": line_prop,
                        "character": char_prop,
                        "language": language_prop,
                    },
                    "required": ["path", "line", "character"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lsp_references",
                "description": (
                    "[lsp] Read-only find-references: every place the symbol at the "
                    "given 0-based position is used. Returns file locations, never edits."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": path_prop,
                        "line": line_prop,
                        "character": char_prop,
                        "include_declaration": {
                            "type": "boolean",
                            "description": "Include the declaration itself (default true).",
                        },
                        "language": language_prop,
                    },
                    "required": ["path", "line", "character"],
                },
            },
        },
    ]


# ── rendering ────────────────────────────────────────────────────


def _uri_to_display(uri: str, project_root: Path) -> str:
    """Best-effort shorten a ``file://`` URI to a project-relative path for
    the LLM. Falls back to the raw URI if it can't be relativised."""
    if not isinstance(uri, str) or not uri.startswith("file:"):
        return str(uri)
    try:
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        parsed = urlparse(uri)
        local = url2pathname(unquote(parsed.path))
        resolved = Path(local).resolve()
        return str(resolved.relative_to(project_root.resolve()))
    except (ValueError, OSError):
        return uri


def _fmt_range(rng: Any) -> str:
    """LSP range → ``L:C-L:C`` (1-based for human reading)."""
    if not isinstance(rng, dict):
        return "?"
    start = rng.get("start") or {}
    end = rng.get("end") or {}
    sl = start.get("line", 0) + 1
    sc = start.get("character", 0) + 1
    el = end.get("line", 0) + 1
    ec = end.get("character", 0) + 1
    return f"{sl}:{sc}-{el}:{ec}"


_SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}


def _render_diagnostics(diags: list[dict[str, Any]], project_root: Path) -> str:
    if not diags:
        return "No diagnostics — the language server reports this file as clean."
    lines: list[str] = []
    for d in diags:
        rng = d.get("range") or {}
        sev = _SEVERITY.get(d.get("severity"), "diagnostic")
        msg = str(d.get("message", "")).strip().replace("\n", " ")
        source = d.get("source")
        prefix = f"[{source}] " if isinstance(source, str) and source else ""
        lines.append(f"{sev} @ {_fmt_range(rng)}: {prefix}{msg}")
    return "\n".join(lines)


def _render_locations(locs: list[dict[str, Any]], project_root: Path) -> str:
    if not locs:
        return "No locations found."
    lines: list[str] = []
    for loc in locs:
        disp = _uri_to_display(loc.get("uri", ""), project_root)
        lines.append(f"{disp}:{_fmt_range(loc.get('range'))}")
    return "\n".join(lines)


# ── provider ─────────────────────────────────────────────────────


class LspToolProvider:
    """ToolProvider exposing the three read-only LSP tools across every
    running language server.

    `is_enabled()`/`get_schemas()` are driven purely by whether any server
    is actually alive, so a configured-but-stopped server contributes
    nothing. Dispatch picks the server by `language`, then by sole-live
    fallback, then errors out cleanly.
    """

    name = "lsp"

    def is_enabled(self) -> bool:
        return bool(live_clients())

    def get_schemas(self) -> list[dict[str, Any]]:
        # No live server ⇒ no schemas ⇒ the agent never sees lsp_* tools.
        if not live_clients():
            return []
        return _schemas()

    def owns(self, tool_name: str) -> bool:
        return tool_name in _TOOL_NAMES

    # ── server selection ─────────────────────────────────────────

    def _pick_client(self, language: Any) -> tuple[str | None, LspClient | None, str | None]:
        """Return ``(server_id, client, error)``. Exactly one of client /
        error is non-None.

        Selection order: explicit `language` match → the single live server
        if there's only one → error listing what's available.
        """
        clients = live_clients()
        if not clients:
            return None, None, "no running LSP server (start one via /lsp/start)"

        if isinstance(language, str) and language.strip():
            wanted = language.strip().lower()
            for sid, client in clients.items():
                if (client.language or "").lower() == wanted:
                    return sid, client, None
            available = ", ".join(sorted({c.language for c in clients.values()}))
            return None, None, (
                f"no running LSP server for language '{language}' "
                f"(running: {available or 'none'})"
            )

        if len(clients) == 1:
            sid, client = next(iter(clients.items()))
            return sid, client, None

        available = ", ".join(sorted({c.language for c in clients.values()}))
        return None, None, (
            f"multiple LSP servers running ({available}); pass `language` to choose"
        )

    # ── dispatch ─────────────────────────────────────────────────

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Run one LSP tool. NEVER raises — every failure path returns a
        ``{"text": "ERROR: ..."}`` dict so the agent loop stays alive."""
        try:
            return self._dispatch_inner(tool_name, args)
        except LspError as exc:
            return {"text": f"ERROR: LSP request failed: {exc}"}
        except Exception as exc:  # defense-in-depth: dispatch must never escape
            logger.warning("lsp dispatch %r raised: %s", tool_name, exc, exc_info=True)
            return {"text": f"ERROR: {exc}"}

    def _dispatch_inner(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        args = args or {}
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return {"text": "ERROR: 'path' is required"}

        server_id, client, err = self._pick_client(args.get("language"))
        if err is not None or client is None or server_id is None:
            return {"text": f"ERROR: {err}"}

        # Project root = the server's analysis cwd. Without it we cannot
        # safely sandbox the path, so refuse rather than read an arbitrary
        # absolute path.
        root_str = getattr(client, "_cwd", None)
        if not root_str:
            return {"text": "ERROR: LSP server has no project root; restart it with a project_root"}
        project_root = Path(root_str)

        # Sandbox: reject anything resolving outside the project root BEFORE
        # we open the file or talk to the server.
        try:
            target = _resolve_safe(project_root, path)
        except SandboxError as exc:
            return {"text": f"ERROR: {exc}"}
        if not target.is_file():
            return {"text": f"ERROR: not a file or does not exist: {path}"}

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"text": f"ERROR: cannot read {path}: {exc}"}

        uri = _path_to_uri(str(target))
        if not uri:
            return {"text": f"ERROR: cannot form file URI for {path}"}

        language_id = _language_id_for(target, client.language)
        # Load a disk snapshot so the server analyses the current file. This
        # writes nothing back — did_open is purely a read-side notification.
        client.did_open(uri, language_id, text)

        if tool_name == "lsp_diagnostics":
            return self._do_diagnostics(client, server_id, uri, project_root)
        if tool_name == "lsp_definition":
            return self._do_locations(
                client, server_id, uri, args, project_root, kind="definition"
            )
        if tool_name == "lsp_references":
            return self._do_locations(
                client, server_id, uri, args, project_root, kind="references"
            )
        return {"text": f"ERROR: unknown LSP tool '{tool_name}'"}

    # ── per-tool handlers ────────────────────────────────────────

    def _do_diagnostics(
        self, client: LspClient, server_id: str, uri: str, project_root: Path
    ) -> dict[str, Any]:
        diags = client.get_diagnostics(uri)
        if diags is None:
            # No push arrived within settle — likely still indexing.
            return {
                "text": (
                    "Language server has not produced diagnostics yet (still indexing); "
                    "retry shortly."
                ),
                "meta": {"lsp_server": server_id, "truncated": False, "total_results": 0,
                         "note": "not yet ready, retry"},
            }
        total = len(diags)
        shown = diags[:_DIAG_LIMIT]
        body = _render_diagnostics(shown, project_root)
        truncated = total > len(shown)
        if truncated:
            body += f"\n[... {total - len(shown)} more diagnostics omitted]"
        body = _truncate_middle(body, _TEXT_LIMIT)
        return {
            "text": body,
            "meta": {
                "lsp_server": server_id,
                "truncated": truncated or len(body) >= _TEXT_LIMIT,
                "total_results": total,
            },
        }

    def _do_locations(
        self,
        client: LspClient,
        server_id: str,
        uri: str,
        args: dict[str, Any],
        project_root: Path,
        *,
        kind: str,
    ) -> dict[str, Any]:
        line = args.get("line")
        character = args.get("character")
        if not isinstance(line, int) or not isinstance(character, int):
            return {"text": "ERROR: 'line' and 'character' must be integers (0-based)"}

        if kind == "definition":
            locs = client.definition(uri, line, character)
        else:
            include = args.get("include_declaration", True)
            locs = client.references(uri, line, character, include_declaration=bool(include))

        total = len(locs)
        shown = locs[:_RESULT_LIMIT]
        body = _render_locations(shown, project_root)
        truncated = total > len(shown)
        if truncated:
            body += f"\n[... {total - len(shown)} more results omitted]"
        body = _truncate_middle(body, _TEXT_LIMIT)
        return {
            "text": body,
            "meta": {
                "lsp_server": server_id,
                "truncated": truncated or len(body) >= _TEXT_LIMIT,
                "total_results": total,
            },
        }


def build_lsp_providers() -> list[LspToolProvider]:
    """Return ``[LspToolProvider()]`` iff at least one server is live, else
    ``[]``. Same shape as `build_mcp_providers` so the agent-loop wiring is
    a single ``*build_lsp_providers(),`` splat that costs nothing when off.
    """
    if live_clients():
        return [LspToolProvider()]
    return []
