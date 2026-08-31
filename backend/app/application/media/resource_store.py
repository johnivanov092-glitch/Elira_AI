"""Durable RAW resource store (R1) — the server-owned home for uploaded bytes.

A resource is the original file, stored verbatim, plus a small metadata sidecar.
Registration performs NO processing (no STT/OCR/extract/ffmpeg, no network) — it
only writes bytes durably and records safe metadata.

Security posture:
- ``resource_id`` is a cryptographically unpredictable opaque token (hex), so a
  bound run's model cannot guess another resource's id;
- the id is the ONLY thing used to build a path; the user's filename is stored
  as metadata but NEVER becomes a path component;
- ``owner_session`` is a client-supplied tag, not an auth boundary — under the
  strictly-local single-owner model the real secret is the 128-bit ``resource_id``
  (an attacker cannot reach the ownership check without the victim's leaked ref);
- intake streams to a temp file in the target dir, enforces a hard size cap,
  fsyncs, then atomically renames into place (no overwrite); a failed intake
  unlinks its partial file;
- every resolved path is asserted to stay inside the canonical resources root.

No second DB layer: metadata is a per-resource JSON sidecar under the data root
(the same durable data-root pattern the rest of the app uses).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.application.file_extract.runtime import TEXT_EXTS, _AUDIO_EXTS
from app.core.data_files import data_subdir

_RESOURCE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_CONTENT_TYPE_RE = re.compile(r"^[a-z0-9!#$&^_.+\-]+/[a-z0-9!#$&^_.+\-]+$")
_DEFAULT_MAX_RESOURCE_BYTES = 0  # unlimited; disk/OS errors are the physical limit
_MAX_NAME_CHARS = 255
# Intake/meta temp files (and meta-less blobs) older than this are crash debris.
_TEMP_SWEEP_AGE_SECONDS = 3600
_DEFAULT_RETENTION_SECONDS = 30 * 24 * 3600
_STORE_LOCK = threading.RLock()

# ── kind classification (cosmetic for the model; processing support is decided
#    from the extension in `processing`, not from kind) ─────────────────────────
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".wmv", ".mpg", ".mpeg"}
_DOCUMENT_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xls", ".xlsx",
                  ".xlsm", ".rtf", ".odt"}
_ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar"}


class ResourceError(ValueError):
    """A rejected resource operation. ``reason`` is machine-readable; ``http_status``
    maps to the upload route response."""

    def __init__(self, reason: str, http_status: int = 400):
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


class ResourceTooLarge(ResourceError):
    """Intake exceeded the hard size cap."""

    def __init__(self, reason: str = "resource_too_large"):
        super().__init__(reason, http_status=413)


@dataclass(frozen=True)
class ResourceRecord:
    """A durable resource. ``storage_path`` is runtime-only and MUST NOT cross the
    model boundary — use :func:`resource_ref` for anything the model sees."""

    resource_id: str
    original_name: str
    kind: str
    content_type: str
    size: int
    sha256: str
    created_at: float
    owner_session: str
    storage_path: str


def max_resource_bytes() -> int:
    raw = os.environ.get("ELIRA_MAX_RESOURCE_BYTES", "").strip()
    try:
        value = int(raw)
        if value > 0:
            return value
    except ValueError:
        pass
    return _DEFAULT_MAX_RESOURCE_BYTES


def resource_retention_seconds() -> int:
    raw = os.environ.get("ELIRA_RESOURCE_RETENTION_SECONDS", "").strip()
    try:
        value = int(raw)
        if value > 0:
            return value
    except ValueError:
        pass
    return _DEFAULT_RETENTION_SECONDS


def _root() -> Path:
    return data_subdir("resources")


def _blobs_dir() -> Path:
    path = _root() / "blobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _meta_dir() -> Path:
    path = _root() / "meta"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except Exception:  # noqa: BLE001
        return False


def _safe_display_name(name: str) -> str:
    """A metadata-only display name. Basename only (path separators stripped),
    control chars removed, length-capped. This value is NEVER used to build a
    filesystem path — the opaque resource_id is."""
    raw = str(name or "")
    raw = raw.replace("\\", "/").split("/")[-1]        # drop any directory parts
    cleaned = "".join(ch for ch in raw if ch.isprintable() and ch not in "\r\n\t")
    cleaned = cleaned.strip().strip(".") or "file"
    return cleaned[:_MAX_NAME_CHARS]


def classify_kind(name: str, content_type: str) -> str:
    """Return one of audio|video|image|document|archive|other. MIME leads;
    extension resolves the rest. Note: audio-container extensions like .mp4/.webm
    map to video/audio by MIME but remain transcribable (see `processing`)."""
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    ext = Path(_safe_display_name(name)).suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _AUDIO_EXTS and ext not in _VIDEO_EXTS:
        return "audio"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _DOCUMENT_EXTS or ext in TEXT_EXTS:
        return "document"
    if ext in _ARCHIVE_EXTS:
        return "archive"
    return "other"


class _Intake:
    """Streaming intake: write chunks to a temp file in the blobs dir (same
    filesystem → atomic rename), hashing and size-capping as we go, then commit
    to the opaque id or abort (unlinking the partial file)."""

    def __init__(self) -> None:
        self._blobs = _blobs_dir()
        self._cap = max_resource_bytes()
        self._hash = hashlib.sha256()
        self._size = 0
        self._committed = False
        self._closed = False
        fd, tmp_name = tempfile.mkstemp(dir=str(self._blobs), prefix=".intake-", suffix=".part")
        self._fd = fd
        self._tmp_path = Path(tmp_name)

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._size += len(chunk)
        if self._cap > 0 and self._size > self._cap:
            raise ResourceTooLarge()
        self._hash.update(chunk)
        view = memoryview(chunk)
        while view:
            written = os.write(self._fd, view)
            if written <= 0:
                raise OSError("resource intake write made no progress")
            view = view[written:]

    def commit(self, *, original_name: str, content_type: str, owner_session: str) -> ResourceRecord:
        owner = str(owner_session or "").strip()
        if not owner:
            raise ResourceError("owner_required", http_status=400)
        if self._size == 0:
            raise ResourceError("empty_upload", http_status=400)
        os.fsync(self._fd)
        os.close(self._fd)
        self._closed = True

        resource_id = secrets.token_hex(16)
        blob_path = self._blobs / resource_id
        if not _within(self._blobs, blob_path):
            self.abort()
            raise ResourceError("containment_violation", http_status=500)
        if blob_path.exists():        # opaque 128-bit id: astronomically unlikely, still fail-closed
            self.abort()
            raise ResourceError("id_collision", http_status=500)

        raw_ct = str(content_type or "").split(";", 1)[0].strip().lower()[:127]
        ct = raw_ct if _CONTENT_TYPE_RE.fullmatch(raw_ct) else "application/octet-stream"
        name = _safe_display_name(original_name)
        record = ResourceRecord(
            resource_id=resource_id,
            original_name=name,
            kind=classify_kind(name, ct),
            content_type=ct,
            size=self._size,
            sha256=self._hash.hexdigest(),
            created_at=time.time(),
            owner_session=owner,
            storage_path=str(blob_path),
        )
        os.replace(self._tmp_path, blob_path)     # atomic; temp gone
        try:
            _write_meta(record)
        except Exception:
            # Metadata is the durable index — an un-indexed blob is an orphan.
            try:
                blob_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            raise ResourceError("metadata_write_failed", http_status=500)
        self._committed = True
        return record

    def abort(self) -> None:
        if not self._closed:
            try:
                os.close(self._fd)
            except Exception:  # noqa: BLE001
                pass
            self._closed = True
        if not self._committed:
            try:
                self._tmp_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass


def sweep_stale(max_age_seconds: float = _TEMP_SWEEP_AGE_SECONDS,
                retention_seconds: float | None = None) -> None:
    """Best-effort cleanup of crash debris (never raises): partial ``.intake-*`` /
    ``.meta-*`` temp files, and committed blobs whose meta sidecar never landed
    (the crash window between blob rename and meta write). An age threshold keeps
    a concurrent in-flight intake untouched. Handled error paths already unlink
    their own partials — this only reclaims a hard process crash / power loss."""
    try:
        blobs = _blobs_dir()
        meta = _meta_dir()
    except Exception:  # noqa: BLE001
        return
    now = time.time()
    retention = (resource_retention_seconds() if retention_seconds is None
                 else float(retention_seconds))

    def _too_old(path: Path) -> bool:
        try:
            return (now - path.stat().st_mtime) > max_age_seconds
        except Exception:  # noqa: BLE001
            return False

    with _STORE_LOCK:
        for directory, pattern in ((blobs, ".intake-*"), (meta, ".meta-*")):
            for temp in directory.glob(pattern):
                if _too_old(temp):
                    try:
                        temp.unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass
        for blob in blobs.iterdir():
            name = blob.name
            if not _RESOURCE_ID_RE.match(name):    # skip temps / non-resource files
                continue
            if not (meta / f"{name}.json").is_file() and _too_old(blob):
                try:
                    blob.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
        # Durable resources are retained for a bounded period since their last
        # successful lookup. This prevents abandoned uploads from accumulating
        # forever while keeping resources alive across normal runs/restarts.
        if retention > 0:
            for meta_path in meta.glob("*.json"):
                rid = meta_path.stem
                if not _RESOURCE_ID_RE.fullmatch(rid):
                    continue
                blob_path = blobs / rid
                if not blob_path.is_file():
                    if _too_old(meta_path):
                        try:
                            meta_path.unlink(missing_ok=True)
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                try:
                    expired = (now - meta_path.stat().st_mtime) > retention
                except Exception:  # noqa: BLE001
                    expired = False
                if expired:
                    try:
                        blob_path.unlink(missing_ok=True)
                        meta_path.unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass


def new_intake() -> _Intake:
    """Open a streaming intake (used by the upload route to pump async chunks).
    Opportunistically sweeps long-dead crash debris first (best-effort)."""
    sweep_stale()
    return _Intake()


def register_resource(*, original_name: str, content_type: str, owner_session: str,
                      data: bytes) -> ResourceRecord:
    """Convenience one-shot registration from in-memory bytes (tests / small
    callers). Routes should prefer :func:`new_intake` to stream large files."""
    intake = new_intake()
    try:
        intake.write(bytes(data or b""))
        return intake.commit(original_name=original_name, content_type=content_type,
                             owner_session=owner_session)
    except BaseException:
        intake.abort()
        raise


def discard(record: ResourceRecord) -> None:
    """Best-effort removal of a resource's metadata sidecar AND durable blob.

    Used to undo a resource that was just registered but could not be attached to
    any run (e.g. a run-binding failure), so a registration unreachable by every
    run does not leak durable bytes. Never raises. Only unlinks inside the
    canonical meta/blobs roots — a malformed ``storage_path`` can never delete
    elsewhere. Meta is removed FIRST so the resource becomes immediately
    unresolvable (``get_record`` needs the sidecar); a crash between the two
    unlinks leaves an orphan blob that ``sweep_stale`` reclaims."""
    rid = str(record.resource_id or "").strip().lower()
    if not _RESOURCE_ID_RE.match(rid):
        return
    with _STORE_LOCK:
        meta_root = _meta_dir()
        blobs_root = _blobs_dir()
        for path, root in ((meta_root / f"{rid}.json", meta_root),
                           (blobs_root / rid, blobs_root)):
            try:
                if _within(root, path):
                    path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001 — cleanup is best-effort
                pass


def _meta_payload(record: ResourceRecord) -> dict[str, Any]:
    return {
        "resource_id": record.resource_id,
        "original_name": record.original_name,
        "kind": record.kind,
        "content_type": record.content_type,
        "size": record.size,
        "sha256": record.sha256,
        "created_at": record.created_at,
        "owner_session": record.owner_session,
    }


def _write_meta(record: ResourceRecord) -> None:
    meta_dir = _meta_dir()
    meta_path = meta_dir / f"{record.resource_id}.json"
    if meta_path.exists():
        raise ResourceError("id_collision", http_status=500)
    fd, tmp_name = tempfile.mkstemp(dir=str(meta_dir), prefix=".meta-", suffix=".json")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(_meta_payload(record), handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, meta_path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        raise


def get_record(resource_id: str) -> ResourceRecord | None:
    """Resolve a resource by opaque id. Returns None for a malformed id (this is
    what rejects a path passed in place of an id), a missing sidecar, or a missing
    blob. Durable ids may be resolved by any Workflow run; the Workflow permission
    chip is the product authorization boundary."""
    rid = str(resource_id or "").strip().lower()
    if not _RESOURCE_ID_RE.match(rid):
        return None
    with _STORE_LOCK:
        meta_path = _meta_dir() / f"{rid}.json"
        if not meta_path.is_file():
            return None
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        blob_path = _blobs_dir() / rid
        if not blob_path.is_file() or not _within(_blobs_dir(), blob_path):
            return None
        try:
            record = ResourceRecord(
                resource_id=rid,
                original_name=_safe_display_name(payload.get("original_name", "")),
                kind=str(payload.get("kind", "other")),
                content_type=str(payload.get("content_type", "application/octet-stream")),
                size=int(payload.get("size", 0)),
                sha256=str(payload.get("sha256", "")),
                created_at=float(payload.get("created_at", 0.0)),
                owner_session=str(payload.get("owner_session", "")),
                storage_path=str(blob_path),
            )
            try:
                os.utime(meta_path, None)  # last successful access drives retention
            except Exception:  # noqa: BLE001 — lookup remains read-capable
                pass
            return record
        except Exception:  # noqa: BLE001
            return None


def read_bytes(record: ResourceRecord) -> bytes:
    """Read the raw bytes of a resource — runtime-only, for the processing seam.
    Re-asserts containment before touching the filesystem."""
    blob_path = Path(record.storage_path)
    if not _within(_blobs_dir(), blob_path) or not blob_path.is_file():
        raise ResourceError("resource_blob_missing", http_status=404)
    return blob_path.read_bytes()


def resource_ref(record: ResourceRecord) -> dict[str, Any]:
    """The model-safe projection — the ONLY resource shape that crosses the model
    boundary. No absolute/storage path, no bytes, no sha, no owner."""
    return {
        "resource_id": record.resource_id,
        "name": record.original_name,
        "kind": record.kind,
        "content_type": record.content_type,
        "size": record.size,
    }


_COPY_CHUNK = 1024 * 1024
_USE_WINDOWS_RENAME = os.name == "nt"


def _workspace_destination(workspace_root: Path, dest_dir: Path, final_name: str) -> Path:
    """Return a canonical destination or fail before touching the destination."""
    if (not final_name or final_name in {".", ".."}
            or "/" in final_name or "\\" in final_name or "\x00" in final_name):
        raise ResourceError("invalid_destination", http_status=400)
    try:
        root = workspace_root.resolve()
        if not root.is_dir():
            raise ValueError("workspace is not a directory")
        resolved_dir = dest_dir.resolve()
        resolved_dir.relative_to(root)
        candidate = resolved_dir / final_name
        # Path.exists() follows links and is false for a dangling link.  The
        # requested directory entry is occupied in either case and must never be
        # followed as the publication destination.
        if os.path.lexists(candidate):
            raise ResourceError("destination_exists", http_status=409)
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except ResourceError:
        raise
    except Exception as exc:
        raise ResourceError("invalid_destination", http_status=400) from exc
    return resolved


def _best_effort_unlink(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def _copy_to_new_file(
    *,
    source: Path,
    destination_root: Path,
    dest_dir: Path,
    final_name: str,
    cap: int,
    temp_prefix: str,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    source_root: Path | None = None,
) -> tuple[int, str]:
    """Stream ``source`` to a verified, atomically-created destination.

    ``source_root`` enables a final containment/regular-file check immediately
    before opening a workspace source.  The destination root is always supplied
    by the server; using ``dest_dir`` as its own root would make containment
    vacuous for a symlinked download directory.
    """
    dest = _workspace_destination(destination_root, dest_dir, final_name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _workspace_destination(destination_root, dest_dir, final_name)

    write_hasher = hashlib.sha256()
    size = 0
    fd, tmp_name = tempfile.mkstemp(dir=str(dest_dir), prefix=temp_prefix, suffix=".part")
    tmp_path = Path(tmp_name)
    closed = False
    temp_needs_cleanup = True
    try:
        read_source = source
        if source_root is not None:
            try:
                canonical_root = source_root.resolve()
                read_source = source.resolve()
                read_source.relative_to(canonical_root)
            except Exception as exc:  # noqa: BLE001
                raise ResourceError("source_outside_workspace", http_status=400) from exc
            if not read_source.is_file():
                raise ResourceError("source_not_file", http_status=400)

        with open(read_source, "rb") as src:
            while True:
                chunk = src.read(_COPY_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if (cap > 0 and size > cap) or (
                    expected_size is not None and size > expected_size
                ):
                    raise ResourceTooLarge()
                write_hasher.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("copy write made no progress")
                    view = view[written:]

        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        staged_hasher = hashlib.sha256()
        staged_size = 0
        while True:
            chunk = os.read(fd, _COPY_CHUNK)
            if not chunk:
                break
            staged_size += len(chunk)
            staged_hasher.update(chunk)

        wanted_size = size if expected_size is None else expected_size
        wanted_sha256 = (
            write_hasher.hexdigest() if expected_sha256 is None else expected_sha256
        )
        if staged_size != wanted_size or staged_hasher.hexdigest() != wanted_sha256:
            raise ResourceError("integrity_mismatch", http_status=500)

        os.close(fd)
        closed = True
        current_dest = _workspace_destination(destination_root, dest_dir, final_name)
        if current_dest != dest:
            raise ResourceError("invalid_destination", http_status=400)
        try:
            if _USE_WINDOWS_RENAME:
                os.rename(tmp_path, current_dest)
                temp_needs_cleanup = False
            else:
                os.link(tmp_path, current_dest)
                temp_needs_cleanup = not _best_effort_unlink(tmp_path)
        except FileExistsError as exc:
            raise ResourceError("destination_exists", http_status=409) from exc
        return size, write_hasher.hexdigest()
    finally:
        if not closed:
            try:
                os.close(fd)
            except Exception:  # noqa: BLE001
                pass
        if temp_needs_cleanup:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass


def materialize(
    record: ResourceRecord,
    dest_dir: Path,
    final_name: str,
    *,
    workspace_root: Path,
) -> None:
    """Stream a resource's durable blob into ``dest_dir/final_name``.

    Streaming (chunked — never loads the whole blob into RAM), bounded (aborts if
    the source exceeds the recorded size or the hard cap), integrity-checked (the
    streamed size AND sha256 must match the ResourceRecord), atomic + no-overwrite
    (temp file in dest_dir → fsync → atomic rename that fails if the target
    exists). Any failure unlinks the partial temp and raises ResourceError with a
    stable ``reason`` (destination_exists / integrity_mismatch / resource_too_large
    / resource_blob_missing / materialize_failed).

    ``workspace_root`` is server-owned. Containment is rechecked before directory
    creation, after creation, and immediately before the atomic commit.
    This protects against supplied paths and pre-existing reparse points. It is
    not an OS isolation boundary against another process running as the same user,
    which already has project-write access through the normal local tool runtime.
    """
    blob_path = Path(record.storage_path)
    if not _within(_blobs_dir(), blob_path) or not blob_path.is_file():
        raise ResourceError("resource_blob_missing", http_status=404)
    workspace = workspace_root.resolve()
    try:
        _copy_to_new_file(
            source=blob_path,
            destination_root=workspace,
            dest_dir=dest_dir,
            final_name=final_name,
            cap=max_resource_bytes(),
            temp_prefix=".materialize-",
            expected_size=record.size,
            expected_sha256=record.sha256,
        )
    except ResourceError:
        raise
    except Exception as exc:  # noqa: BLE001 — never surface a raw path/exception
        raise ResourceError("materialize_failed", http_status=500) from exc


def publish_copy(
    *,
    workspace_root: Path,
    source: Path,
    destination_root: Path,
    dest_dir: Path,
    final_name: str,
    cap: int | None = None,
    expected_sha256: str | None = None,
) -> tuple[int, str]:
    """Stream a REGULAR in-workspace file into ``dest_dir/final_name`` (a single
    name component) for delivery via the existing download route.

    Streaming (chunked — never loads the whole file into RAM), bounded by ``cap``,
    integrity-checked (the staged copy is read back and its size + sha256 must
    match what was written), atomic + no-overwrite (temp in dest_dir → fsync →
    atomic rename/link that FAILS if the target exists). Re-asserts the source is
    a regular file inside ``workspace_root`` immediately before reading (defends
    against a supplied path or a pre-existing reparse point — not against an
    equal-privilege concurrent process, which already has project-write). Returns
    (size, sha256_hex); on any failure unlinks the temp and raises ResourceError
    with a stable reason (source_outside_workspace / source_not_file /
    destination_exists / integrity_mismatch / resource_too_large / publish_failed).
    """
    limit = cap if cap is not None else max_resource_bytes()
    root = workspace_root.resolve()
    # Re-check containment + regular-file RIGHT before reading.
    try:
        real_src = Path(source).resolve()
        real_src.relative_to(root)
    except Exception as exc:  # noqa: BLE001
        raise ResourceError("source_outside_workspace", http_status=400) from exc
    if not real_src.is_file():        # rejects dir / device / fifo / socket / missing
        raise ResourceError("source_not_file", http_status=400)

    try:
        return _copy_to_new_file(
            source=real_src,
            source_root=root,
            destination_root=destination_root,
            dest_dir=dest_dir,
            final_name=final_name,
            cap=limit,
            temp_prefix=".publish-",
            expected_sha256=expected_sha256,
        )
    except ResourceError:
        raise
    except Exception as exc:  # noqa: BLE001 — never surface a raw path/exception
        raise ResourceError("publish_failed", http_status=500) from exc
