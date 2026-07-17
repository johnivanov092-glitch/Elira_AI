"""Remote OCR worker transport (R5C) — a narrow, server-owned HTTP client.

Speaks the fixed ``/v1`` protocol of the trusted OCR worker (see the server repo
``docker/ocr``): ``GET /v1/capabilities`` then ``POST /v1/jobs/ocr``. Every
connection detail — URL, pinned CA, bearer token, timeout, input cap — comes from
process env ONLY; the model never supplies any of it. The client talks to the
TLS gateway, which injects its own internal credential; the client sends only the
bearer.

Security posture:
- empty URL/token or any misconfiguration → :class:`RemoteUnavailable` BEFORE any
  file is read or any request is sent;
- HTTPS REQUIRES an explicitly configured, existing pinned CA file (``verify`` is
  that path — never ``False``);
- plain HTTP is refused unless ``ELIRA_REMOTE_WORKER_ALLOW_INSECURE=1`` (a lab
  escape hatch), and in that mode the Authorization header is NOT sent;
- exactly one request per call — no retries;
- the response is size-bounded WHILE streaming, before any JSON parsing;
- the worker's returned fields are strictly type/shape/bounds validated; a raw
  server message is never trusted or surfaced.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
from urllib.parse import urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_MAX_INPUT_BYTES = 50 * 1024 * 1024      # matches the server default
_DEFAULT_TIMEOUT_SECONDS = 660.0
_MIN_TIMEOUT_SECONDS = 5.0
_MAX_TIMEOUT_SECONDS = 870.0                      # stays below the executor's 900s class
_CAPABILITIES_READ_CAP = 64 * 1024
_JOB_RESPONSE_READ_CAP = 8 * 1024 * 1024 + 64 * 1024   # server caps serialized body at 8 MiB
_CAPABILITIES_PATH = "/v1/capabilities"
_JOB_PATH = "/v1/jobs/ocr"
_HEX64_RE = re.compile(r"[0-9a-f]{64}")


class RemoteWorkerError(Exception):
    """Base for remote-worker failures. ``reason`` is an internal (never public)
    code used only for server-side logging."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RemoteUnavailable(RemoteWorkerError):
    """The worker is not configured or the configuration is unsafe/incomplete."""


class RemoteTransportError(RemoteWorkerError):
    """A network error, timeout, or non-200 HTTP status."""


class RemoteProtocolError(RemoteWorkerError):
    """A malformed, oversized, or contract-violating response."""


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def max_input_bytes() -> int:
    """The client-local input cap (bytes). Env override, else 50 MiB."""
    raw = _env("ELIRA_REMOTE_MAX_INPUT_BYTES")
    try:
        value = int(raw)
        if value > 0:
            return value
    except ValueError:
        pass
    return _DEFAULT_MAX_INPUT_BYTES


def _timeout_seconds() -> float:
    raw = _env("ELIRA_REMOTE_TIMEOUT_SECONDS")
    try:
        value = float(raw)
    except ValueError:
        value = _DEFAULT_TIMEOUT_SECONDS
    if value != value:                             # NaN
        value = _DEFAULT_TIMEOUT_SECONDS
    return max(_MIN_TIMEOUT_SECONDS, min(_MAX_TIMEOUT_SECONDS, value))


@dataclasses.dataclass(frozen=True)
class WorkerConfig:
    base_url: str
    verify: str            # CA bundle path for https; "" for http (unused)
    token: str
    send_auth: bool        # False in the insecure-http lab mode
    timeout: float


@dataclasses.dataclass(frozen=True)
class WorkerCapabilities:
    operations: tuple[str, ...]
    max_upload_bytes: int


@dataclasses.dataclass(frozen=True)
class WorkerJobPayload:
    """A validated, bounded projection of the worker's JobResponse. Carries the
    OCR text (which the caller registers as a resource) but NO transport detail."""

    text: str
    text_sha256: str
    page_count: int
    confidence: float
    processing_ms: int


def resolve_config() -> WorkerConfig:
    """Build a validated config from env, or raise :class:`RemoteUnavailable`.

    Performs NO I/O beyond an ``os.path.isfile`` on the pinned CA. Never reads the
    resource and never contacts the worker."""
    url = _env("ELIRA_REMOTE_WORKER_URL")
    if not url:
        raise RemoteUnavailable("url_not_configured")
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https") or not parts.hostname:
        raise RemoteUnavailable("url_invalid")

    token = _env("ELIRA_REMOTE_WORKER_TOKEN")
    if not token:
        raise RemoteUnavailable("token_not_configured")

    # Rebuild the base URL from scheme/host/port/path ONLY — dropping any userinfo
    # (and query/fragment). httpx turns ``http://user:pass@host`` into an
    # automatic ``Authorization: Basic`` header, which would both defeat the
    # "no Authorization in insecure mode" rule and put a credential on a plaintext
    # wire. The bearer is the only auth we send, and only over https.
    netloc = parts.hostname if parts.port is None else f"{parts.hostname}:{parts.port}"
    base_url = urlunsplit((scheme, netloc, parts.path, "", "")).rstrip("/")
    if scheme == "https":
        ca = _env("ELIRA_REMOTE_WORKER_CA")
        # HTTPS demands an explicit, existing pinned CA. We never fall back to the
        # system trust store and never disable verification.
        if not ca or not os.path.isfile(ca):
            raise RemoteUnavailable("tls_ca_missing")
        return WorkerConfig(base_url=base_url, verify=ca, token=token,
                            send_auth=True, timeout=_timeout_seconds())

    # Plain HTTP: lab-only, and gated behind an explicit opt-in.
    if _env("ELIRA_REMOTE_WORKER_ALLOW_INSECURE") != "1":
        raise RemoteUnavailable("insecure_http_forbidden")
    # In the insecure lab mode the bearer is deliberately NOT put on the wire.
    return WorkerConfig(base_url=base_url, verify="", token=token,
                        send_auth=False, timeout=_timeout_seconds())


class WorkerClient:
    """One-shot transport over the fixed ``/v1`` OCR protocol.

    ``transport`` is an optional httpx transport for tests; when set, TLS
    verification is bypassed by httpx itself (no real socket), which is why the
    HTTPS-requires-CA rule is enforced in :func:`resolve_config`, before a client
    is ever built."""

    def __init__(self, config: WorkerConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        self._config = config
        self._transport = transport

    def _open(self) -> httpx.Client:
        kwargs: dict = {
            "base_url": self._config.base_url,
            "timeout": httpx.Timeout(self._config.timeout, connect=min(10.0, self._config.timeout)),
            "follow_redirects": False,
            # Ignore ambient HTTP(S)_PROXY / .netrc: the worker URL is server-owned
            # and must be reached directly, never redirected by env or given
            # credentials from a netrc file.
            "trust_env": False,
            # Known limitation (accepted): httpx emits an INFO "HTTP Request: <url>"
            # line on the shared "httpx" logger for every call, so the worker HOST
            # (non-secret, server-owned LAN infra — like ELIRA_STT_URL) appears in
            # app logs. The bearer token and CA path (the secrets) never do: the
            # token is a header, the URL has its userinfo stripped in resolve_config,
            # and this module logs only stable reason codes. Quieting the httpx
            # logger is deliberately NOT done here — it is a process-wide logging
            # change (mcp_http_client also uses httpx) and out of R5C's scope.
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        else:
            # A pinned CA path for https; True (never False) for http where TLS is
            # not in play anyway.
            kwargs["verify"] = self._config.verify or True
        return httpx.Client(**kwargs)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._config.send_auth:
            headers["Authorization"] = f"Bearer {self._config.token}"
        if extra:
            headers.update(extra)
        return headers

    def _read_bounded(self, method: str, path: str, *, cap: int,
                      extra_headers: dict[str, str] | None = None, **kw) -> bytes:
        """Send exactly one request and return the response body, bounded by *cap*
        (enforced while streaming, before any parse). Raises on status != 200,
        transport error, or an oversized body."""
        headers = self._headers(extra_headers)
        with self._open() as client:
            try:
                with client.stream(method, path, headers=headers, **kw) as resp:
                    if resp.status_code != 200:
                        # The error body (server envelope) is deliberately NOT read
                        # or trusted; the status alone drives a local code.
                        raise RemoteTransportError(f"status_{resp.status_code}")
                    buffer = bytearray()
                    for chunk in resp.iter_bytes():
                        buffer += chunk
                        if len(buffer) > cap:
                            raise RemoteProtocolError("response_too_large")
                    return bytes(buffer)
            except (RemoteTransportError, RemoteProtocolError):
                raise
            except httpx.HTTPError as exc:
                # No retry: a single failure surfaces as a transport error.
                raise RemoteTransportError("transport") from exc

    def capabilities(self) -> WorkerCapabilities:
        raw = self._read_bounded("GET", _CAPABILITIES_PATH, cap=_CAPABILITIES_READ_CAP)
        return _parse_capabilities(raw)

    def run_ocr_job(self, *, filename: str, content_type: str, data: bytes,
                    content_sha256: str) -> WorkerJobPayload:
        """POST the resource bytes as a single multipart request.

        Passing in-memory ``bytes`` makes httpx build a fully-sized multipart body
        with an explicit Content-Length and no Transfer-Encoding (the smuggling
        surface the worker refuses)."""
        files = {"file": (filename or "upload", data, content_type or "application/octet-stream")}
        raw = self._read_bounded(
            "POST", _JOB_PATH, cap=_JOB_RESPONSE_READ_CAP,
            extra_headers={"x-content-sha256": content_sha256}, files=files,
        )
        return _parse_job(raw)


def _load_json(raw: bytes, reason: str) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RemoteProtocolError(reason) from exc
    if not isinstance(data, dict):
        raise RemoteProtocolError(reason)
    return data


def _int_field(data: dict, key: str, reason: str, *, minimum: int) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RemoteProtocolError(reason)
    return value


def _parse_capabilities(raw: bytes) -> WorkerCapabilities:
    data = _load_json(raw, "capabilities_not_json")
    ops = data.get("operations")
    if not isinstance(ops, list) or not all(isinstance(o, str) for o in ops):
        raise RemoteProtocolError("capabilities_operations")
    max_upload = _int_field(data, "max_upload_bytes", "capabilities_max_upload", minimum=1)
    return WorkerCapabilities(operations=tuple(ops), max_upload_bytes=max_upload)


def _parse_job(raw: bytes) -> WorkerJobPayload:
    data = _load_json(raw, "job_not_json")
    if data.get("operation") != "ocr":
        raise RemoteProtocolError("job_operation")
    text = data.get("text")
    if not isinstance(text, str):
        raise RemoteProtocolError("job_text")
    digest = data.get("text_sha256")
    if not isinstance(digest, str) or not _HEX64_RE.fullmatch(digest):
        raise RemoteProtocolError("job_digest_shape")
    page_count = _int_field(data, "page_count", "job_page_count", minimum=0)
    processing_ms = _int_field(data, "processing_ms", "job_processing_ms", minimum=0)
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise RemoteProtocolError("job_confidence")
    try:
        confidence = float(confidence)                # a huge JSON int would OverflowError
    except (OverflowError, ValueError):
        raise RemoteProtocolError("job_confidence")
    if confidence != confidence or not (0.0 <= confidence <= 1.0):     # NaN / out of range
        raise RemoteProtocolError("job_confidence_range")
    return WorkerJobPayload(
        text=text, text_sha256=digest, page_count=page_count,
        confidence=confidence, processing_ms=processing_ms,
    )
