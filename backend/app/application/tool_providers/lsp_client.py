"""Minimal LSP (Language Server Protocol) stdio client — read-only.

Speaks JSON-RPC 2.0 over a language server's stdin/stdout pipes, the
transport every editor-grade server (pyright, typescript-language-server,
gopls, rust-analyzer, ...) ships with. HTTP/socket transport is out of
scope.

This is a sibling of `mcp_client.py`, but LSP differs in three ways that
forbid sharing the MCP reader:

  1. **Framing.** LSP wraps every message in an HTTP-style header block —
     ``Content-Length: N\\r\\n\\r\\n`` followed by exactly N bytes of UTF-8
     JSON. MCP is line-delimited (one JSON object per ``readline()``). So
     the pipes here are **binary** (``text=False``) and we parse headers +
     a fixed byte count by hand.
  2. **Diagnostics are push.** A server reports problems via the
     ``textDocument/publishDiagnostics`` *notification* (no request id),
     not as a response. We cache the latest push per-URI and let callers
     poll that cache after ``did_open``.
  3. **Read-only by construction.** This client deliberately implements
     ONLY ``initialize``/``initialized``, ``textDocument/didOpen``,
     ``textDocument/definition``, ``textDocument/references`` and the
     ``shutdown``/``exit`` teardown. It has no ``didChange``, ``didSave``,
     ``formatting``, ``rename``, ``codeAction`` or ``executeCommand`` — it
     never asks a server to mutate anything. ``did_open`` loads a snapshot
     the caller already read from disk; it writes nothing.

Lifecycle:
    client = LspClient("python", "pyright-langserver", ["--stdio"], root_uri)
    client.start()                              # spawn + reader thread + handshake
    client.did_open(uri, "python", source_text) # notify → triggers diagnostics push
    diags = client.get_diagnostics(uri)         # poll the push-cache
    locs = client.definition(uri, line, char)   # request/response
    refs = client.references(uri, line, char)
    client.stop()                               # shutdown/exit → process-tree kill

LSP coordinates are **0-based** (line 0 = first line, char 0 = first column).

Thread model mirrors McpClient: one daemon reader thread blocked on the
binary stdout, resolving a per-id ``threading.Event`` so requesters wake.
Server-initiated requests and notifications other than publishDiagnostics
are logged-and-dropped — we are a read-only consumer and answer nothing.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

# Reuse the battle-tested process-group spawn + Windows tree-kill from the
# code-agent shell tools rather than re-deriving them. A bare ``proc.kill()``
# orphans children on Windows; ``_kill_proc_tree`` runs ``taskkill /F /T``.
from app.application.code_agent.tools import (
    _kill_proc_tree,
    _new_process_group_kwargs,
)


logger = logging.getLogger(__name__)


JSONRPC_VERSION = "2.0"

# A language server may need to index a project before it answers. The
# handshake gets a generous budget; ordinary requests are short.
DEFAULT_REQUEST_TIMEOUT = 15.0
INITIALIZE_TIMEOUT = 60.0
# How long get_diagnostics waits for a publishDiagnostics push to settle
# after did_open before giving up and returning whatever is cached.
DEFAULT_DIAGNOSTICS_SETTLE = 5.0

# Bounded tail kept from the server's stderr for crash diagnostics.
_STDERR_TAIL_CAP = 8000


def _uri_cache_key(uri: str) -> str:
    """Canonical diagnostics key for equivalent file-URI spellings.

    Pyright on Windows accepts ``file:///D:/...`` but publishes diagnostics
    under ``file:///d%3A/...``. LSP document identities are URI based, so the
    cache must collapse those representations without altering the URI sent
    on the wire.
    """
    try:
        parsed = urlsplit(uri)
    except ValueError:
        return uri
    if parsed.scheme.casefold() != "file":
        return uri
    path = unquote(parsed.path).replace("\\", "/")
    if len(path) >= 3 and path[0] == "/" and path[1].isalpha() and path[2] == ":":
        path = f"/{path[1].lower()}{path[2:]}"
    if os.name == "nt":
        path = path.casefold()
    return f"file://{parsed.netloc.casefold()}{path}"


class LspError(Exception):
    """Raised on protocol-level failures (timeout, JSON-RPC error response,
    server crash). The LSP **provider** wraps these into
    ``{"text": "ERROR: ..."}`` so they never bubble out of dispatch."""


@dataclass
class _PendingRequest:
    event: threading.Event = field(default_factory=threading.Event)
    response: Optional[dict[str, Any]] = None


class LspClient:
    """One client = one language-server subprocess.

    Construction is cheap; the subprocess is spawned by ``.start()``. Each
    instance owns:
      * the Popen object (binary stdin/stdout pipes)
      * a background thread reading Content-Length frames from stdout
      * a dict of in-flight request_id → _PendingRequest
      * a per-URI cache of the latest pushed diagnostics
    """

    def __init__(
        self,
        language: str,
        command: str,
        args: Optional[list[str]] = None,
        root_uri: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        *,
        cwd: Optional[str] = None,
    ) -> None:
        self.language = language
        self._command = command
        self._args = list(args or [])
        self._root_uri = root_uri
        self._env = env
        self._cwd = cwd

        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._stderr_reader: Optional[threading.Thread] = None

        self._id_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, _PendingRequest] = {}
        self._pending_lock = threading.Lock()

        # uri → list[diagnostic dict]; replaced wholesale on each push.
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._diag_lock = threading.Lock()
        self._opened_documents: dict[str, str] = {}
        self._document_versions: dict[str, int] = {}

        self._stderr_tail = ""
        self._stopped = False
        self._initialized = False

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Spawn the server, start the reader, run the LSP handshake.

        Raises LspError if the process can't spawn or the initialize
        request times out / errors. The runtime catches this and records
        it in _LAST_ERROR — it never reaches the agent.
        """
        if self._proc is not None:
            return
        try:
            self._proc = subprocess.Popen(
                [self._command, *self._args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env,
                cwd=self._cwd,
                bufsize=0,  # unbuffered binary — we frame by hand
                **_new_process_group_kwargs(),
            )
        except (OSError, ValueError) as exc:
            raise LspError(f"failed to spawn language server '{self._command}': {exc}") from exc

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_reader.start()

        # A failed handshake must not leak the subprocess. Tear it down
        # (process-tree kill) before re-raising, mirroring McpClient.start.
        try:
            self._handshake()
        except BaseException:
            self.stop()
            raise

    def _handshake(self) -> None:
        # Minimal, read-only client capabilities. We only consume
        # diagnostics + definition + references, so we advertise just
        # enough for a server to enable them and nothing more.
        params: dict[str, Any] = {
            "processId": None,
            "rootUri": self._root_uri,
            "capabilities": {
                "workspace": {
                    "configuration": True,
                    "workspaceFolders": True,
                },
                "window": {"workDoneProgress": True},
                "textDocument": {
                    "publishDiagnostics": {"relatedInformation": False},
                    "definition": {"linkSupport": True},
                    "references": {},
                    "synchronization": {
                        "didSave": False,
                        "willSave": False,
                        "dynamicRegistration": False,
                    },
                },
            },
            "clientInfo": {"name": "elira-lsp", "version": "1.0"},
        }
        if self._root_uri:
            params["workspaceFolders"] = [{"uri": self._root_uri, "name": "root"}]

        self._request("initialize", params, timeout=INITIALIZE_TIMEOUT)
        self._notify("initialized", {})
        self._initialized = True

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        """Best-effort graceful shutdown, then a hard process-tree kill.

        Sequence: ``shutdown`` request → ``exit`` notification → close
        stdin → wait → ``_kill_proc_tree`` (NOT a naive ``proc.kill()``,
        which orphans children on Windows) → wake every pending request
        with an LspError so no caller blocks forever.
        """
        if self._stopped:
            return
        self._stopped = True
        proc = self._proc
        if proc is None:
            return

        if proc.poll() is None:
            try:
                self._request("shutdown", None, timeout=2.0)
            except Exception:
                pass
            try:
                self._notify("exit", None)
            except Exception:
                pass

        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass

        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            # Server ignored shutdown — take down the whole tree.
            _kill_proc_tree(proc)
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass

        self._wake_all_pending(LspError("language server stopped"))

    # ── read-only operations ─────────────────────────────────────

    def did_open(self, uri: str, language_id: str, text: str) -> None:
        """Tell the server a document is open so it starts analysing it.

        This is a *notification*: it triggers an asynchronous
        ``publishDiagnostics`` push, which lands in the cache that
        ``get_diagnostics`` polls. We only ever send a snapshot the caller
        already read from disk — nothing is written back.
        """
        cache_key = _uri_cache_key(uri)
        previous_text = self._opened_documents.get(cache_key)
        if previous_text == text:
            # Diagnostics are a push cache. Re-sending didOpen for an already
            # open, unchanged document is invalid LSP and many real servers do
            # not publish again; keeping the cache makes retries deterministic.
            return
        if previous_text is not None:
            self._notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        with self._diag_lock:
            # Drop any stale push for this uri so get_diagnostics waits for
            # the fresh one rather than returning the previous open's result.
            self._diagnostics.pop(cache_key, None)
        version = self._document_versions.get(cache_key, 0) + 1
        self._document_versions[cache_key] = version
        self._opened_documents[cache_key] = text
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": version,
                    "text": text,
                }
            },
        )

    def get_diagnostics(
        self, uri: str, settle: float = DEFAULT_DIAGNOSTICS_SETTLE
    ) -> Optional[list[dict[str, Any]]]:
        """Return the diagnostics the server pushed for ``uri``.

        Diagnostics are eventually-consistent: after ``did_open`` the push
        may take a moment (or, on a cold large project, longer than
        ``settle``). Polls the cache up to ``settle`` seconds.

        Returns the diagnostics list (possibly empty = "analysed, clean")
        once a push has arrived, or ``None`` if none arrived in time —
        which the provider surfaces as "not ready yet, retry".
        """
        deadline = None if settle is None else time.monotonic() + max(0.0, float(settle))
        step = 0.05
        latest: Optional[list[dict[str, Any]]] = None
        while True:
            with self._diag_lock:
                cache_key = _uri_cache_key(uri)
                if cache_key in self._diagnostics:
                    latest = list(self._diagnostics[cache_key])
                    # Some servers (notably rust-analyzer) publish an empty
                    # warm-up snapshot before their real diagnostics. A
                    # non-empty snapshot is actionable immediately; an empty
                    # one is returned only after the settle budget expires.
                    if latest:
                        return latest
            if not self.is_alive():
                return latest
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return latest
                threading.Event().wait(min(step, remaining))
            else:
                threading.Event().wait(step)

    def definition(self, uri: str, line: int, character: int) -> list[dict[str, Any]]:
        """textDocument/definition → normalized list of {uri, range}.

        The server may answer with a single Location, a Location[], or a
        LocationLink[] (the linkSupport variant). All three are flattened
        to a uniform ``[{"uri", "range"}]`` shape. Coordinates 0-based.
        """
        result = self._request(
            "textDocument/definition",
            {
                "textDocument": {"uri": uri},
                "position": {"line": int(line), "character": int(character)},
            },
        )
        return _normalize_locations(result)

    def references(
        self,
        uri: str,
        line: int,
        character: int,
        include_declaration: bool = True,
    ) -> list[dict[str, Any]]:
        """textDocument/references → list of {uri, range}. 0-based coords."""
        result = self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": int(line), "character": int(character)},
                "context": {"includeDeclaration": bool(include_declaration)},
            },
        )
        return _normalize_locations(result)

    # ── JSON-RPC plumbing ────────────────────────────────────────

    def _request(
        self, method: str, params: Any, timeout: float = DEFAULT_REQUEST_TIMEOUT
    ) -> Any:
        with self._id_lock:
            self._next_id += 1
            req_id = self._next_id

        pending = _PendingRequest()
        with self._pending_lock:
            self._pending[req_id] = pending

        message: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": req_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params

        try:
            self._send(message)
        except LspError:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise

        if not pending.event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise LspError(f"request '{method}' timed out after {timeout}s")

        response = pending.response or {}
        with self._pending_lock:
            self._pending.pop(req_id, None)

        if "error" in response and response["error"] is not None:
            err = response["error"]
            raise LspError(f"{method} failed: {err.get('message', err)}")
        return response.get("result")

    def _notify(self, method: str, params: Any) -> None:
        message: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def _send(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise LspError("server process is not running")
        # Poll first: on Windows writing to a stdin whose process already
        # exited may not raise synchronously, so we'd silently lose the
        # message and then time out. Detect the dead process up front.
        if proc.poll() is not None:
            raise LspError("server process has exited")
        try:
            with self._send_lock:
                proc.stdin.write(_frame(message))
                proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise LspError(f"failed to write to server: {exc}") from exc

    def _read_loop(self) -> None:
        """Read Content-Length framed messages from the binary stdout until
        EOF, dispatching each to a pending request or the diagnostics cache.
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        stream = proc.stdout
        try:
            while True:
                message = _read_frame(stream)
                if message is None:
                    break  # EOF
                self._handle_message(message)
        except Exception:
            logger.debug("lsp reader loop ended", exc_info=True)
        finally:
            # Process/pipe gone — unblock anyone still waiting.
            self._wake_all_pending(LspError("language server closed the connection"))

    def _handle_message(self, message: dict[str, Any]) -> None:
        msg_id = message.get("id")
        method = message.get("method")

        # A response carries an id and no method.
        if msg_id is not None and method is None:
            with self._pending_lock:
                pending = self._pending.get(msg_id)
            if pending is not None:
                pending.response = message
                pending.event.set()
            return

        # Real servers such as Pyright block analysis until their
        # workspace/configuration request is answered. Reply to the small,
        # read-only subset needed for diagnostics/navigation; explicitly
        # reject mutation requests and unknown methods so the server never
        # waits forever for this client.
        if msg_id is not None and isinstance(method, str):
            params = message.get("params") or {}
            if method == "workspace/configuration":
                items = params.get("items") if isinstance(params, dict) else None
                result: Any = [None] * len(items) if isinstance(items, list) else []
                response = {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": result}
            elif method == "workspace/workspaceFolders":
                folders = (
                    [{"uri": self._root_uri, "name": "root"}]
                    if self._root_uri
                    else None
                )
                response = {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": folders}
            elif method in {
                "client/registerCapability",
                "client/unregisterCapability",
                "window/workDoneProgress/create",
            }:
                response = {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": None}
            elif method == "workspace/applyEdit":
                response = {
                    "jsonrpc": JSONRPC_VERSION,
                    "id": msg_id,
                    "result": {
                        "applied": False,
                        "failureReason": "Elira LSP client is read-only",
                    },
                }
            else:
                response = {
                    "jsonrpc": JSONRPC_VERSION,
                    "id": msg_id,
                    "error": {"code": -32601, "message": "method not supported"},
                }
            try:
                self._send(response)
            except LspError:
                logger.debug("failed to answer LSP server request %s", method, exc_info=True)
            return

        if method == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            uri = params.get("uri")
            if isinstance(uri, str):
                diags = params.get("diagnostics")
                with self._diag_lock:
                    self._diagnostics[_uri_cache_key(uri)] = (
                        list(diags) if isinstance(diags, list) else []
                    )
            return
        # Any other notification/server-request is dropped intentionally.

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in iter(proc.stderr.readline, b""):
                try:
                    line = raw.decode("utf-8", errors="replace")
                except Exception:
                    continue
                # Keep only a bounded tail so a chatty server can't grow this
                # without limit.
                self._stderr_tail = (self._stderr_tail + line)[-_STDERR_TAIL_CAP:]
        except Exception:
            pass

    def _wake_all_pending(self, error: LspError) -> None:
        with self._pending_lock:
            pendings = list(self._pending.values())
            self._pending.clear()
        for pending in pendings:
            if pending.response is None:
                pending.response = {"error": {"message": str(error)}}
            pending.event.set()

    @property
    def stderr_tail(self) -> str:
        return self._stderr_tail


# ── framing helpers ──────────────────────────────────────────────


def _frame(message: dict[str, Any]) -> bytes:
    """Serialize a JSON-RPC message to an LSP frame:
    ``Content-Length: N\\r\\n\\r\\n`` + N bytes of UTF-8 JSON body."""
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _read_frame(stream: Any) -> Optional[dict[str, Any]]:
    """Read one Content-Length framed JSON message from a binary stream.

    Returns the parsed dict, or ``None`` at EOF. Headers may arrive split
    from the body across separate writes (a server is free to flush the
    header and the body independently) — ``readline()`` and a fixed-count
    ``read()`` both block until the bytes are available, so partial frames
    are handled transparently.
    """
    content_length: Optional[int] = None
    # Read headers up to the blank line.
    while True:
        line = stream.readline()
        if line == b"":
            return None  # EOF
        line = line.strip()
        if line == b"":
            break  # end of headers
        if b":" in line:
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                try:
                    content_length = int(value.strip())
                except ValueError:
                    content_length = None

    if content_length is None or content_length < 0:
        # Malformed frame with no usable length — can't recover position.
        raise LspError("LSP frame missing Content-Length header")

    # Read exactly content_length bytes (read() may return short on pipes).
    chunks: list[bytes] = []
    remaining = content_length
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None  # EOF mid-body
        chunks.append(chunk)
        remaining -= len(chunk)
    body = b"".join(chunks)
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise LspError(f"invalid JSON in LSP frame: {exc}") from exc


def _normalize_locations(result: Any) -> list[dict[str, Any]]:
    """Flatten definition/references results to ``[{"uri", "range"}]``.

    Accepts: None, a single Location, a Location[], or a LocationLink[].
    A LocationLink uses ``targetUri``/``targetSelectionRange`` instead of
    ``uri``/``range``.
    """
    if result is None:
        return []
    items = result if isinstance(result, list) else [result]
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "uri" in item and "range" in item:
            out.append({"uri": item["uri"], "range": item["range"]})
        elif "targetUri" in item:
            out.append(
                {
                    "uri": item["targetUri"],
                    "range": item.get("targetSelectionRange") or item.get("targetRange"),
                }
            )
    return out
