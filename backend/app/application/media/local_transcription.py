"""Server-owned LOCAL transcription runtime (R3) — faster-whisper / CTranslate2.

The first REAL local GPU/CPU workload behind the general resource_process
execution-target framework (R2). Every parameter — model, device, compute_type,
cache dir, beam size, timeout — is SERVER-OWNED and allowlisted or structurally
validated; the model never supplies any of them. The model only reaches this via
``resource_process(operation="transcribe", execution_target="local_gpu"|
"local_cpu")``, which hands the runtime the INTERNAL resource path.

Honesty: the capability probe reports ready ONLY when the runtime prerequisites
are present (NVIDIA GPU, faster-whisper + CTranslate2, CUDA device, loadable
cuBLAS/cuDNN, writable model cache) AND the transcribe function is wired. It
NEVER loads a Whisper model, so first-load/model failures remain execution
failures and are handled by the existing strict/auto routing contract. Until the pinned deps are provisioned,
faster-whisper/CTranslate2 are absent, so local_gpu/local_cpu stay UNAVAILABLE
and ``auto`` falls through to server_gpu.

Model loading is lazy, thread-safe, single-flight, and bounded (a small model
cache keyed by model/device/compute_type — never one instance per call). No
temp files are created: faster-whisper decodes the durable blob path directly
(one workload for mp4/ogg/m4a/wav/mp3/flac/webm/aac — no extension router).

Limitation (documented, not hidden): a CTranslate2 in-process transcribe cannot
be truly cancelled by a Python thread timeout. The existing tool timeout (>=3630s)
is a soft outer bound only; a real hard-cancel would require a process-boundary
worker, deferred until cancellation is a proven need (see resource_process
contract §7). We do NOT call a thread timeout a real cancellation.
"""
from __future__ import annotations

import ctypes
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── server-owned configuration (allowlisted; never model-supplied) ────────────

_MODEL_ENV = "ELIRA_LOCAL_STT_MODEL"
_COMPUTE_ENV = "ELIRA_LOCAL_STT_COMPUTE_TYPE"
_CACHE_DIR_ENV = "ELIRA_LOCAL_STT_CACHE_DIR"

# Known CTranslate2 Whisper model ids only — an arbitrary Hugging Face repo from
# the model/env is refused (fail-closed to the default).
_ALLOWED_MODELS = frozenset({
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3",
    "large-v3-turbo", "distil-large-v3", "distil-medium.en", "distil-small.en",
})
# Quality-first default for the target workstation. Model loading is lazy and can
# download into the configured cache on first use; capability probing never
# claims that the model is already cached.
_DEFAULT_MODEL = "large-v3"

_GPU_COMPUTE_TYPES = frozenset({"int8_float16", "float16", "int8"})
_CPU_COMPUTE_TYPES = frozenset({"int8", "int8_float32", "float32"})
_DEFAULT_GPU_COMPUTE = "int8_float16"
_DEFAULT_CPU_COMPUTE = "int8"

_BEAM_SIZE = 5                        # server-owned; never model-supplied
_MAX_TEXT_CHARS = 20000              # bounded output (the adapter clips again)
_MAX_MODELS = 2                      # bounded model cache
_RUNTIME_WAIT_SECONDS = 1.0           # fail fast if another local STT is stuck


class LocalTranscriptionError(RuntimeError):
    """A local-runtime failure with a stable machine code; the message never
    carries a raw exception string, model path, or device detail."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _resolve_model() -> str:
    raw = str(os.environ.get(_MODEL_ENV, "") or "").strip()
    return raw if raw in _ALLOWED_MODELS else _DEFAULT_MODEL


def _resolve_compute_type(device: str) -> str:
    raw = str(os.environ.get(_COMPUTE_ENV, "") or "").strip().lower()
    if device == "cuda":
        return raw if raw in _GPU_COMPUTE_TYPES else _DEFAULT_GPU_COMPUTE
    return raw if raw in _CPU_COMPUTE_TYPES else _DEFAULT_CPU_COMPUTE


def _resolve_cache_dir() -> str:
    """Server-owned model cache directory under the data root by default. An env
    override is accepted only if it is an absolute, creatable, writable path; any
    unsafe/unknown value fails closed to the default. Never returned to the
    model/frontend."""
    raw = str(os.environ.get(_CACHE_DIR_ENV, "") or "").strip()
    if raw:
        try:
            candidate = Path(raw)
            if candidate.is_absolute():
                candidate.mkdir(parents=True, exist_ok=True)
                if os.access(candidate, os.W_OK):
                    return str(candidate)
        except Exception:  # noqa: BLE001 — fail closed to the default
            pass
    from app.core.data_files import data_subdir
    return str(data_subdir("model_cache"))


def _cache_dir_usable() -> bool:
    try:
        with tempfile.TemporaryFile(dir=_resolve_cache_dir()):
            return True
    except Exception:  # noqa: BLE001
        return False


# ── CUDA runtime discovery (server-owned; no model load) ─────────────────────

_CUDA_STATE_LOCK = threading.RLock()
_CUDA_DIRECTORY_HANDLES: list[Any] = []
_CUDA_DIRECTORY_PATHS: set[str] = set()
_CUDA_LIBRARY_HANDLES: dict[str, Any] = {}

def _ensure_cuda_dll_path() -> None:
    """On Windows, add the DLL directories of the pip-installed CUDA runtime
    packages (nvidia-cublas-cu12 / nvidia-cudnn-cu12) so CTranslate2 can load
    cuBLAS/cuDNN. Best-effort and idempotent; only ever adds known package dirs,
    never a model- or env-supplied path. No-op when the packages are absent."""
    if os.name != "nt":
        return
    import importlib.util
    with _CUDA_STATE_LOCK:
        for pkg in ("nvidia.cublas", "nvidia.cudnn"):
            try:
                spec = importlib.util.find_spec(pkg)
            except Exception:  # noqa: BLE001
                spec = None
            if spec is None or not spec.submodule_search_locations:
                continue
            for base in spec.submodule_search_locations:
                bin_dir = Path(base) / "bin"
                resolved = str(bin_dir.resolve())
                if not bin_dir.is_dir() or resolved in _CUDA_DIRECTORY_PATHS:
                    continue
                try:
                    # The directory is removed from the DLL search path when this
                    # handle is closed/collected, so retain it for process life.
                    handle = os.add_dll_directory(resolved)
                except Exception:  # noqa: BLE001
                    continue
                _CUDA_DIRECTORY_HANDLES.append(handle)
                _CUDA_DIRECTORY_PATHS.add(resolved)


def _load_cuda_library(name: str) -> Any:
    loader = ctypes.WinDLL if os.name == "nt" else ctypes.CDLL
    return loader(name)


def _cuda_runtime_libraries_ready() -> bool:
    """Verify the speech runtime's direct CUDA libraries without loading a model."""
    names = (
        (
            "cublas64_12.dll",
            "cudnn64_9.dll",
            "cudnn_ops64_9.dll",
            "cudnn_graph64_9.dll",
            "cudnn_engines_precompiled64_9.dll",
            "cudnn_engines_runtime_compiled64_9.dll",
            "cudnn_heuristic64_9.dll",
            "cudnn_adv64_9.dll",
            "cudnn_cnn64_9.dll",
        )
        if os.name == "nt"
        else (
            "libcublas.so.12",
            "libcudnn.so.9",
            "libcudnn_ops.so.9",
            "libcudnn_graph.so.9",
            "libcudnn_engines_precompiled.so.9",
            "libcudnn_engines_runtime_compiled.so.9",
            "libcudnn_heuristic.so.9",
            "libcudnn_adv.so.9",
            "libcudnn_cnn.so.9",
        )
    )
    with _CUDA_STATE_LOCK:
        for name in names:
            if name in _CUDA_LIBRARY_HANDLES:
                continue
            try:
                _CUDA_LIBRARY_HANDLES[name] = _load_cuda_library(name)
            except Exception:  # noqa: BLE001 — missing/incompatible runtime
                return False
    return True


# ── capability probes (no model load; fail-closed; TTL-cached, thread-safe) ───

def _has_module(name: str) -> bool:
    from app.application.media import execution
    return execution._has_module(name)


def _import_runtime_modules() -> Any | None:
    """Import the complete STT runtime without constructing/loading a model."""
    try:
        import faster_whisper  # noqa: F401
        import ctranslate2
    except Exception:  # noqa: BLE001 — partial/broken optional install
        return None
    return ctranslate2


def gpu_runtime_ready() -> tuple[bool, str, tuple[str, ...]]:
    """(detected, device_label, runtimes) for LOCAL GPU transcription. Ready only
    if a NVIDIA GPU is present, faster-whisper + CTranslate2 import, CTranslate2
    sees a CUDA device, and the cache dir is writable. Never loads a model."""
    from app.application.media import execution

    def _probe() -> tuple[bool, str, tuple[str, ...]]:
        gpu = execution._detect_nvidia_gpu()
        if not gpu:
            return (False, "local-gpu", ())
        if not (_has_module("faster_whisper") and _has_module("ctranslate2")):
            return (False, gpu, ())
        try:
            _ensure_cuda_dll_path()
            if not _cuda_runtime_libraries_ready():
                return (False, gpu, ("faster-whisper", "ctranslate2"))
            runtime = _import_runtime_modules()
            if runtime is None or int(runtime.get_cuda_device_count()) < 1:
                return (False, gpu, ("faster-whisper",))
            if _resolve_compute_type("cuda") not in runtime.get_supported_compute_types("cuda"):
                return (False, gpu, ("faster-whisper",))
        except Exception:  # noqa: BLE001 — CUDA libs missing / init failure
            return (False, gpu, ("faster-whisper",))
        if not _cache_dir_usable():
            return (False, gpu, ("faster-whisper", "ctranslate2"))
        return (True, gpu, ("faster-whisper", "ctranslate2"))

    return execution._cached("r3_local_gpu_ready", _probe)


def cpu_runtime_ready() -> tuple[bool, str, tuple[str, ...]]:
    """(detected, device_label, runtimes) for LOCAL CPU transcription — the same
    faster-whisper runtime with device='cpu'. Ready only when the runtime imports
    and the cache dir is writable. Never loads a model."""
    from app.application.media import execution

    def _probe() -> tuple[bool, str, tuple[str, ...]]:
        if not (_has_module("faster_whisper") and _has_module("ctranslate2")):
            return (False, "cpu", ())
        runtime = _import_runtime_modules()
        if runtime is None:
            return (False, "cpu", ())
        if _resolve_compute_type("cpu") not in runtime.get_supported_compute_types("cpu"):
            return (False, "cpu", ())
        if not _cache_dir_usable():
            return (False, "cpu", ("faster-whisper", "ctranslate2"))
        return (True, "cpu", ("faster-whisper", "ctranslate2"))

    return execution._cached("r3_local_cpu_ready", _probe)


# ── lazy, thread-safe, bounded, single-flight model cache ─────────────────────

_RUNTIME_LOCK = threading.Lock()
_MODEL_CACHE_LOCK = threading.RLock()
_MODELS: "dict[tuple[str, str, str, str], Any]" = {}

# Injectable factory (model, device, compute_type, cache_dir) -> model object.
# Default is the real faster-whisper loader; tests inject a fake so the suite
# needs no GPU and downloads no model.
_model_factory: Callable[[str, str, str, str], Any] | None = None


def set_model_factory(factory: Callable[[str, str, str, str], Any] | None) -> None:
    """Override the model loader (tests). None restores the real loader."""
    global _model_factory
    _model_factory = factory


def clear_model_cache() -> None:
    with _MODEL_CACHE_LOCK:
        _MODELS.clear()


def _load_model(model: str, device: str, compute_type: str, cache_dir: str) -> Any:
    factory = _model_factory
    if factory is not None:
        return factory(model, device, compute_type, cache_dir)
    _ensure_cuda_dll_path()
    from faster_whisper import WhisperModel
    return WhisperModel(model, device=device, compute_type=compute_type,
                        download_root=cache_dir)


def _get_model_unlocked(model: str, device: str, compute_type: str, cache_dir: str) -> Any:
    key = (model, device, compute_type, cache_dir)
    with _MODEL_CACHE_LOCK:
        cached = _MODELS.get(key)
        if cached is not None:
            return cached
        # Evict before constructing the replacement. Loading first would briefly
        # hold MAX_MODELS + 1 heavyweight instances and can OOM an 8 GiB GPU.
        if len(_MODELS) >= _MAX_MODELS:
            _MODELS.pop(next(iter(_MODELS)), None)
    instance = _load_model(model, device, compute_type, cache_dir)
    with _MODEL_CACHE_LOCK:
        _MODELS[key] = instance
        return instance


def _get_model(model: str, device: str, compute_type: str, cache_dir: str) -> Any:
    # Direct callers use the same single-flight boundary as transcribe_local.
    with _RUNTIME_LOCK:
        return _get_model_unlocked(model, device, compute_type, cache_dir)


def _extract_text(segments: Any) -> str:
    parts: list[str] = []
    remaining = _MAX_TEXT_CHARS
    for segment in segments:
        if remaining <= 0:
            break
        piece = str(getattr(segment, "text", "") or "")[:remaining]
        parts.append(piece)
        remaining -= len(piece)
        if remaining <= 0:
            break
    return "".join(parts).strip()


def transcribe_local(path: str, *, target: str) -> str:
    """Transcribe the audio at the INTERNAL runtime *path* on *target*
    (local_gpu | local_cpu). All model/device/compute/cache params are
    server-owned. Raises LocalTranscriptionError with a stable code (never a raw
    exception/model path/device string). No temp files are created."""
    if target == "local_gpu":
        device = "cuda"
    elif target == "local_cpu":
        device = "cpu"
    else:
        raise LocalTranscriptionError("invalid_local_target")

    model_id = _resolve_model()
    compute_type = _resolve_compute_type(device)
    cache_dir = _resolve_cache_dir()
    # Serialize local model use so an in-use model cannot be evicted while a new
    # key loads. Acquisition is bounded: a stuck in-process inference cannot make
    # every later local request wait forever; auto routing can fall through.
    if not _RUNTIME_LOCK.acquire(timeout=_RUNTIME_WAIT_SECONDS):
        raise LocalTranscriptionError("local_runtime_busy")
    try:
        try:
            model = _get_model_unlocked(model_id, device, compute_type, cache_dir)
        except Exception as exc:  # noqa: BLE001 — model load / CUDA failure
            logger.warning(
                "local_transcription_load_failed target=%s exception_class=%s",
                target, type(exc).__name__,
            )
            raise LocalTranscriptionError("model_load_failed") from None
        try:
            segments, _info = model.transcribe(path, beam_size=_BEAM_SIZE, vad_filter=True)
            return _extract_text(segments)
        except Exception as exc:  # noqa: BLE001 — decode / inference failure
            logger.warning(
                "local_transcription_failed target=%s exception_class=%s",
                target, type(exc).__name__,
            )
            raise LocalTranscriptionError("transcription_failed") from None
    finally:
        _RUNTIME_LOCK.release()


# ── adapter wiring functions (bound into execution.default_adapters) ──────────

def gpu_transcribe_fn(path: str, filename: str | None = None) -> str:
    """Wired as the local_gpu adapter's transcribe_fn. Ignores filename (the
    container extension is carried by the path; faster-whisper decodes it)."""
    return transcribe_local(path, target="local_gpu")


def cpu_transcribe_fn(path: str, filename: str | None = None) -> str:
    """Wired as the local_cpu adapter's transcribe_fn."""
    return transcribe_local(path, target="local_cpu")
