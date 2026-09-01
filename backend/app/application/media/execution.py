"""Execution targets + workload adapters (R2) — where a resource op runs.

A small, general selector over ``(operation, execution_target)``. It is NOT
STT-specific: transcription is only the FIRST workload adapter. Adding OCR /
image inference / embeddings later means adding another WorkloadAdapter for
its (operation, target) pair — the ResourceRef, attachment pipeline, approval
framework, run binding, and this selector's shape do not change.

Execution targets are GENERAL compute homes, not modes:
- ``local_cpu``  — this host's CPU (always present for pure-Python ops);
- ``local_gpu``  — this host's GPU via a locally-installed runtime;
- ``server_cpu`` — the existing CPU STT service (ELIRA_STT_URL);
- ``auto``       — deterministic pick: local_gpu → server_cpu → local_cpu.

``server_gpu`` remains an accepted legacy input alias but is normalized before
selection, results, and telemetry are emitted.

Capability probes are cheap, bounded, best-effort and HONEST: fixed argv,
shell=False, short timeouts, TTL-cached, thread-safe, no secrets / env / absolute
paths in the projection, and NEVER run a heavy model. Unavailable is never
reported as available.
"""
from __future__ import annotations

import importlib.util
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

from app.application.media import resource_store

logger = logging.getLogger(__name__)


class ResourceOperation(str, Enum):
    INSPECT = "inspect"
    EXTRACT_TEXT = "extract_text"
    TRANSCRIBE = "transcribe"


class ExecutionTarget(str, Enum):
    AUTO = "auto"
    LOCAL_GPU = "local_gpu"
    LOCAL_CPU = "local_cpu"
    SERVER_CPU = "server_cpu"
    # Input compatibility only. Never emit this value in new results/telemetry.
    SERVER_GPU = "server_gpu"


# Operations that only ever run on the local CPU (pure-Python; no GPU/server).
_LOCAL_ONLY_OPERATIONS = frozenset({ResourceOperation.INSPECT.value, ResourceOperation.EXTRACT_TEXT.value})
# Deterministic auto order for a GPU-capable workload (transcribe).
_AUTO_ORDER = (ExecutionTarget.LOCAL_GPU.value, ExecutionTarget.SERVER_CPU.value, ExecutionTarget.LOCAL_CPU.value)

_MAX_RESULT_CHARS = 20000
_STT_TIMEOUT_SECONDS = 3600
_PROBE_TIMEOUT_SECONDS = 4.0
_CACHE_TTL_SECONDS = 60.0
_MAX_DEVICE_LABEL = 80
_MAX_PROBE_BYTES = 4096


@dataclass(frozen=True)
class Capability:
    """The public, model-safe projection of one execution target's readiness for
    an operation. No env, tokens, hostnames, or absolute paths — ever."""

    target: str
    available: bool
    device: str
    runtimes: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

def _clean_label(text: str) -> str:
    """A bounded, printable device label with no path/secret characters."""
    cleaned = "".join(ch for ch in str(text or "") if ch.isprintable() and ch not in "\r\n\t")
    cleaned = cleaned.replace("\\", " ").replace("/", " ").strip()
    return cleaned[:_MAX_DEVICE_LABEL] or "unknown"


# ── cheap cached probes (fixed argv, shell=False, bounded, never raise) ───────

_cache_lock = threading.RLock()
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, producer: Callable[[], Any]) -> Any:
    # Keep the producer inside the lock. Probes are short and infrequent, while
    # duplicate concurrent subprocess/network probes make the advertised cache
    # semantics non-deterministic.
    with _cache_lock:
        now = time.monotonic()
        hit = _cache.get(key)
        if hit is not None and (now - hit[0]) < _CACHE_TTL_SECONDS:
            return hit[1]
        value = producer()
        _cache[key] = (time.monotonic(), value)
        return value


def clear_capability_cache() -> None:
    """Drop the probe cache (tests / after a runtime install)."""
    with _cache_lock:
        _cache.clear()


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:  # noqa: BLE001
        return False


def _detect_nvidia_gpu() -> str | None:
    """Return a safe GPU name label via nvidia-smi, or None. Fixed argv, shell=False,
    short timeout, bounded output; never raises."""
    def _probe() -> str | None:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return None
        try:
            # Temporary files let the child write without an unbounded in-memory
            # PIPE. Only the first bounded chunk is ever read back.
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                proc = subprocess.run(
                    [exe, "--query-gpu=name", "--format=csv,noheader"],
                    stdout=stdout, stderr=stderr,
                    timeout=_PROBE_TIMEOUT_SECONDS, shell=False,
                )
                stdout.seek(0)
                raw = stdout.read(_MAX_PROBE_BYTES + 1)
        except Exception:  # noqa: BLE001
            return None
        if proc.returncode != 0:
            return None
        text = raw[:_MAX_PROBE_BYTES].decode("utf-8", errors="replace")
        line = text.strip().splitlines()[0].strip() if text.strip() else ""
        return _clean_label(line) if line else None
    return _cached("nvidia_gpu", _probe)


def _detect_local_gpu_transcribe() -> tuple[bool, str, tuple[str, ...]]:
    """(available, device_label, runtimes) for a LOCAL GPU transcription runtime.
    Cheap: module presence + GPU presence, never imports/loads a model."""
    def _probe() -> tuple[bool, str, tuple[str, ...]]:
        gpu = _detect_nvidia_gpu()
        runtimes: list[str] = []
        if _has_module("faster_whisper") and _has_module("ctranslate2"):
            runtimes.append("faster-whisper")
        available = bool(gpu) and bool(runtimes)
        return available, (gpu or "local-gpu"), tuple(runtimes)

    return _cached("local_gpu_transcribe", _probe)


def _detect_local_cpu_transcribe() -> tuple[bool, str, tuple[str, ...]]:
    """(available, device_label, runtimes) for a LOCAL CPU transcription runtime."""
    def _probe() -> tuple[bool, str, tuple[str, ...]]:
        runtimes: list[str] = []
        if _has_module("faster_whisper"):
            runtimes.append("faster-whisper")
        # "main" is intentionally excluded: it is far too generic to prove a
        # whisper.cpp runtime and caused false-positive local_cpu capability.
        for binary in ("whisper-cli", "whisper.cpp"):
            if shutil.which(binary):
                runtimes.append("whisper.cpp")
                break
        return (bool(runtimes), "cpu", tuple(runtimes))

    return _cached("local_cpu_transcribe", _probe)


def _server_stt_available() -> bool:
    """Return live readiness of the server-owned STT endpoint.

    The existing STT status call is bounded and the result is TTL-cached here.
    Merely having a default/configured URL is not enough to advertise the target
    as available, because that would prevent ``auto`` from selecting local CPU
    when the server is down. The URL and raw status payload never leave runtime.
    """
    def _probe() -> bool:
        try:
            from app.application.voice.runtime import stt_status

            status = stt_status()
            return isinstance(status, dict) and status.get("ok") is True
        except Exception:  # noqa: BLE001
            return False
    return _cached("server_stt", _probe)


# ── result helpers (stable codes; no exception text / path) ───────────────────

def _fail(operation: str, resource_id: str, code: str, message: str,
          execution_target: str = "") -> dict[str, Any]:
    out = {"ok": False, "operation": operation, "resource_id": resource_id,
           "error": code, "text": f"ERROR: {message}"}
    if execution_target:
        out["execution_target"] = execution_target
    return out


def _transcript_result(record: "resource_store.ResourceRecord", text: str, target: str) -> dict[str, Any]:
    clipped = str(text or "").strip()[:_MAX_RESULT_CHARS]
    return {"ok": True, "operation": ResourceOperation.TRANSCRIBE.value,
            "resource_id": record.resource_id, "kind": record.kind,
            "execution_target": target, "chars": len(clipped), "text": clipped}


# ── workload adapters ─────────────────────────────────────────────────────────

class WorkloadAdapter(Protocol):
    operation: str
    target: str
    backend: str

    def capability(self) -> Capability: ...

    def run(self, record: "resource_store.ResourceRecord") -> dict[str, Any]: ...


@dataclass
class ServerCpuTranscribeAdapter:
    """Reuses ONLY the existing server-owned STT runtime (ELIRA_STT_URL). Never
    accepts a URL/host from the model; transport timeout is 3600s."""

    operation: str = ResourceOperation.TRANSCRIBE.value
    target: str = ExecutionTarget.SERVER_CPU.value
    backend: str = "server-stt"
    health_fn: Callable[[], bool] | None = None
    transcribe_fn: Callable[..., str] | None = None

    def capability(self) -> Capability:
        try:
            ok = bool((self.health_fn or _server_stt_available)())
        except Exception:  # noqa: BLE001 — probes are always fail-closed
            ok = False
        return Capability(
            target=self.target, available=ok, device="remote-stt",
            runtimes=("whisper-stt",) if ok else (),
            operations=(ResourceOperation.TRANSCRIBE.value,) if ok else (),
            limitations=("server-owned STT service; not local hardware",),
        )

    def run(self, record: "resource_store.ResourceRecord") -> dict[str, Any]:
        rid = record.resource_id
        fn = self.transcribe_fn
        if fn is None:
            from app.application.voice.runtime import transcribe as fn  # noqa: PLW0127
        try:
            data = resource_store.read_bytes(record)
            text = fn(data, filename=record.original_name, language=None,
                      timeout=_STT_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 — never surface raw STT error/payload/host
            logger.warning(
                "resource_transcription_failed target=%s resource_id=%s error=%s exception_class=%s",
                self.target, rid, "transcription_failed", type(exc).__name__,
            )
            return _fail(self.operation, rid, "transcription_failed",
                         "speech-to-text failed", self.target)
        text = str(text or "").strip()
        if not text:
            logger.warning(
                "resource_transcription_failed target=%s resource_id=%s error=%s",
                self.target, rid, "transcription_empty",
            )
            return _fail(self.operation, rid, "transcription_empty",
                         "speech-to-text returned no text", self.target)
        return _transcript_result(record, text, self.target)


# Compatibility for imports only. Instances use the canonical server_cpu target.
ServerGpuTranscribeAdapter = ServerCpuTranscribeAdapter


@dataclass
class _LocalTranscribeAdapter:
    """Local (CPU or GPU) transcription. In R2 no local STT runtime is installed,
    so ``capability().available`` is honestly False and ``run`` returns the strict
    ``<target>_unavailable`` error. The runtime wiring (faster-whisper) is R3 —
    ``transcribe_fn`` is injectable so the routing contract is testable now."""

    target: str
    detect_fn: Callable[[], tuple[bool, str, tuple[str, ...]]]
    transcribe_fn: Callable[..., str] | None = None
    backend: str = "faster-whisper"
    operation: str = ResourceOperation.TRANSCRIBE.value

    def capability(self) -> Capability:
        # available MUST use the SAME predicate as run(): a target is available
        # only if its runtime is BOTH detected AND wired. In R2 no local
        # transcribe_fn is wired (that is R3), so local targets are honestly
        # unavailable even on a host where the GPU + faster-whisper are installed —
        # otherwise `auto` would commit to local_gpu and dead-end instead of
        # falling through to the working server STT.
        try:
            detected, device, runtimes = self.detect_fn()
        except Exception:  # noqa: BLE001 — probes are always fail-closed
            detected, device, runtimes = False, self.target, ()
        wired = self.transcribe_fn is not None
        available = bool(detected) and wired
        if available:
            limitations: tuple[str, ...] = ()
        elif detected and not wired:
            limitations = ("local runtime detected but not yet enabled",)
        else:
            limitations = ("no local transcription runtime installed",)
        return Capability(
            target=self.target, available=available, device=_clean_label(device),
            runtimes=runtimes,
            operations=(ResourceOperation.TRANSCRIBE.value,) if available else (),
            limitations=limitations,
        )

    def run(self, record: "resource_store.ResourceRecord") -> dict[str, Any]:
        rid = record.resource_id
        cap = self.capability()
        if not cap.available or self.transcribe_fn is None:
            return _fail(self.operation, rid, f"{self.target}_unavailable",
                         f"{self.target} transcription runtime is not available", self.target)
        try:
            text = self.transcribe_fn(record.storage_path, filename=record.original_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "resource_transcription_failed target=%s resource_id=%s error=%s exception_class=%s",
                self.target, rid, "transcription_failed", type(exc).__name__,
            )
            return _fail(self.operation, rid, "transcription_failed",
                         "speech-to-text failed", self.target)
        text = str(text or "").strip()
        if not text:
            logger.warning(
                "resource_transcription_failed target=%s resource_id=%s error=%s",
                self.target, rid, "transcription_empty",
            )
            return _fail(self.operation, rid, "transcription_empty",
                         "speech-to-text returned no text", self.target)
        return _transcript_result(record, text, self.target)


# ── bounded adapter set + selector ───────────────────────────────────────────

@dataclass(frozen=True)
class AdapterSet:
    """A small immutable set of media workload strategies.

    This is not a tool registry or provider stack: the canonical agent tool still
    executes once through the existing kernel/provider path. These strategies
    only choose the implementation of that already-authorized media operation.
    """

    adapters: tuple[WorkloadAdapter, ...] = field(default_factory=tuple)

    def get(self, operation: str, target: str) -> WorkloadAdapter | None:
        return next((adapter for adapter in self.adapters
                     if adapter.operation == operation and adapter.target == target), None)

def default_adapters() -> AdapterSet:
    # R3: wire the REAL local faster-whisper runtime. capability() stays honest
    # (available == detected AND wired): the strict readiness probes report ready
    # only when the runtime prerequisites are present, so until the pinned deps are
    # provisioned the local targets are unavailable and auto falls through to
    # server_cpu. Imported lazily to avoid a media-package import cycle.
    from app.application.media import local_transcription as lt
    return AdapterSet((
        _LocalTranscribeAdapter(ExecutionTarget.LOCAL_GPU.value, lt.gpu_runtime_ready,
                                transcribe_fn=lt.gpu_transcribe_fn),
        ServerCpuTranscribeAdapter(),
        _LocalTranscribeAdapter(ExecutionTarget.LOCAL_CPU.value, lt.cpu_runtime_ready,
                                transcribe_fn=lt.cpu_transcribe_fn),
    ))


_DEFAULT_ADAPTERS = default_adapters()


def _adapter_set(adapters: AdapterSet | None) -> AdapterSet:
    return adapters if adapters is not None else _DEFAULT_ADAPTERS


@dataclass(frozen=True)
class Candidate:
    """One server-owned step in the deterministic auto execution plan."""

    target: str
    adapter: WorkloadAdapter | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Selection:
    """A routing decision. Either ``adapter`` is set (dispatch it), or ``local`` is
    True (run the pure-local op in processing), or ``error`` is a stable code."""

    target: str
    adapter: WorkloadAdapter | None = None
    local: bool = False
    error: str | None = None
    message: str = ""
    available_targets: tuple[str, ...] = ()
    fallback_chain: tuple[dict[str, str], ...] = ()
    candidates: tuple[Candidate, ...] = ()


def adapter_capability(adapter: WorkloadAdapter) -> Capability:
    """Read one adapter capability without letting a faulty probe escape."""
    try:
        return adapter.capability()
    except Exception:  # noqa: BLE001
        return Capability(adapter.target, False, "unknown")


def valid_execution_target(target: str) -> bool:
    return target in accepted_execution_targets()


def accepted_execution_targets() -> tuple[str, ...]:
    """Model/API inputs, including the temporary legacy alias."""
    return tuple(target.value for target in ExecutionTarget)


def normalize_execution_target(target: str) -> str:
    """Normalize accepted compatibility aliases to the canonical target name."""
    value = str(target or ExecutionTarget.AUTO.value).strip().lower()
    if value == ExecutionTarget.SERVER_GPU.value:
        return ExecutionTarget.SERVER_CPU.value
    return value


def capability_catalog(adapters: AdapterSet | None = None) -> list[dict[str, Any]]:
    """Public projection of every execution target. local_cpu always lists the
    pure-local ops it can always do (inspect/extract_text) plus transcribe when a
    local CPU runtime exists."""
    adapter_set = _adapter_set(adapters)
    catalog: list[dict[str, Any]] = []
    for target in (ExecutionTarget.LOCAL_GPU.value, ExecutionTarget.SERVER_CPU.value,
                   ExecutionTarget.LOCAL_CPU.value):
        adapter = adapter_set.get(ResourceOperation.TRANSCRIBE.value, target)
        cap = adapter_capability(adapter) if adapter is not None else Capability(target, False, "unknown")
        ops = list(cap.operations)
        available = cap.available
        if target == ExecutionTarget.LOCAL_CPU.value:
            # inspect/extract_text always run on the local CPU.
            ops = sorted(set(ops) | set(_LOCAL_ONLY_OPERATIONS))
            available = True
        catalog.append({
            "target": target, "available": available, "device": cap.device,
            "runtimes": list(cap.runtimes), "operations": ops,
            "limitations": list(cap.limitations),
        })
    return catalog


def available_execution_targets(
    operation: str,
    adapters: AdapterSet | None = None,
) -> tuple[str, ...]:
    """Canonical targets currently available for one operation, in route order."""
    op = str(operation or "").strip().lower()
    return tuple(
        str(item["target"])
        for item in capability_catalog(adapters)
        if item.get("available") is True and op in set(item.get("operations") or ())
    )


def select(operation: str, requested_target: str,
           adapters: AdapterSet | None = None) -> Selection:
    """Resolve (operation, requested_target) to a Selection per the R2 routing
    semantics. Fallback is allowed ONLY for auto; strict targets never fall back."""
    adapter_set = _adapter_set(adapters)
    op = str(operation or "").strip().lower()
    raw_target = str(requested_target or ExecutionTarget.AUTO.value).strip().lower()
    if not valid_execution_target(raw_target):
        return Selection(target=raw_target, error="invalid_execution_target",
                         message=f"execution_target must be one of "
                                 f"{sorted(t.value for t in ExecutionTarget)}")
    target = normalize_execution_target(raw_target)

    # inspect / extract_text: local_cpu only.
    if op in _LOCAL_ONLY_OPERATIONS:
        if target in (ExecutionTarget.AUTO.value, ExecutionTarget.LOCAL_CPU.value):
            return Selection(target=ExecutionTarget.LOCAL_CPU.value, local=True)
        return Selection(
            target=target, error="target_not_supported_for_operation",
            message=f"{op} runs only on local_cpu (auto selects it); {target} is not supported",
            available_targets=(ExecutionTarget.LOCAL_CPU.value,))

    # transcribe: adapter-dispatched.
    if op == ResourceOperation.TRANSCRIBE.value:
        if target == ExecutionTarget.AUTO.value:
            # Build references up front but probe lazily in priority order. A
            # healthy local_gpu selection never performs a server health request.
            candidates = [
                Candidate(target=t, adapter=adapter_set.get(op, t))
                for t in _AUTO_ORDER
            ]
            fallback_chain: list[dict[str, str]] = []
            for index, candidate in enumerate(candidates):
                adapter = candidate.adapter
                if adapter is not None and adapter_capability(adapter).available:
                    return Selection(target=candidate.target, adapter=adapter,
                                     available_targets=(candidate.target,),
                                     fallback_chain=tuple(fallback_chain),
                                     candidates=tuple(candidates))
                reason = f"{candidate.target}_unavailable"
                candidates[index] = Candidate(target=candidate.target, reason=reason)
                fallback_chain.append({"target": candidate.target, "reason": reason})
            return Selection(target=ExecutionTarget.AUTO.value,
                             error="no_execution_target_available",
                             message="no transcription execution target is available",
                             available_targets=(),
                             fallback_chain=tuple(fallback_chain),
                             candidates=tuple(candidates))
        # strict target — no fallback.
        adapter = adapter_set.get(op, target)
        if adapter is None:
            return Selection(target=target, error="invalid_execution_target",
                             message=f"no adapter for transcribe on {target}")
        if not adapter_capability(adapter).available:
            return Selection(target=target, error=f"{target}_unavailable",
                             message=f"{target} is not available for transcription")
        return Selection(target=target, adapter=adapter,
                         available_targets=(target,))

    return Selection(target=target, error="unknown_operation",
                     message="unsupported resource operation")
