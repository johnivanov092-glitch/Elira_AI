from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.code_agent.tools._sandbox import _resolve_safe

# ── Encoding-safe file IO ────────────────────────────────────────────────────
# The code-agent operates on whatever source tree the user opens, which on
# Windows commonly includes files saved in legacy 8-bit codepages (cp1251 /
# cp866 for Russian, cp1252 for Western European) as well as utf-16. Reading
# those as utf-8 with errors="replace" silently turned every non-ASCII byte into
# U+FFFD, and then a write/edit re-saved the mangled text as utf-8 — destroying
# the original content. We detect the encoding from the raw bytes and, on
# write/edit, round-trip back into the file's *original* encoding so an edit to
# one line can't corrupt the rest of the file.

try:
    from charset_normalizer import from_bytes as _cn_from_bytes  # type: ignore

    _HAS_CN = True
except Exception:  # pragma: no cover - dependency missing → fall back to perebor
    _cn_from_bytes = None  # type: ignore
    _HAS_CN = False

_BOM_UTF8 = b"\xef\xbb\xbf"
_BOM_UTF16_LE = b"\xff\xfe"
_BOM_UTF16_BE = b"\xfe\xff"

# Codecs charset-normalizer reports that we normalise to a friendlier/correct
# equivalent on write. cp866 round-trips byte-identically through cp1125 but the
# library labels it cp1125; writing back as cp866 keeps the file's real codepage.
_CODEC_ALIASES = {
    "cp1125": "cp866",
}

# Ordered fallback when charset-normalizer is unavailable: try the strictest
# (no replacement) decoders first, ending at cp1252 which decodes nearly any
# byte sequence (last-resort bucket for Western European text).
_FALLBACK_CODECS = ("utf-8", "utf-16", "cp1251", "cp866", "cp1252")


def _normalize_codec(codec: str) -> str:
    return _CODEC_ALIASES.get(codec.lower().replace("-", "_"), codec)


def _to_text_newlines(text: str) -> str:
    """Collapse CRLF / lone CR to LF, matching the universal-newline behaviour the
    code previously got for free from Path.read_text. We now read raw bytes (to
    detect the encoding), which skips newline translation — so callers that expect
    \\n-terminated lines (read output, edit/write diffs) would otherwise see \\r\\n.
    The file's bytes are still written back verbatim; this only normalises the
    decoded *string* we hand to the rest of the agent."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _looks_binary(raw: bytes) -> bool:
    """Heuristic: a file is binary if its first 8 KiB hold a NUL byte or a high
    ratio of non-text control characters. Keeps us from numbering a PNG."""
    sample = raw[:8192]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    # Allowed control bytes: tab, LF, CR, FF and anything >= 0x20.
    text_control = {0x09, 0x0A, 0x0C, 0x0D}
    nontext = sum(1 for b in sample if b < 0x20 and b not in text_control)
    return nontext / len(sample) > 0.30


def _detect_encoding(raw: bytes, *, strict: bool) -> tuple[str, bool] | None:
    """Return (codec, had_bom) for ``raw`` or None when undecodable.

    BOMs are authoritative and checked first. Otherwise charset-normalizer picks
    the codec; in ``strict`` mode (write/edit of an existing file) we additionally
    require low chaos *and* a byte-exact round-trip, so a misdetection can never
    silently rewrite the file in the wrong codepage. ``strict=False`` (read) is
    more forgiving and the caller degrades to cp1252+replace if this returns None.
    """
    if raw.startswith(_BOM_UTF8):
        return ("utf-8", True)
    if raw.startswith(_BOM_UTF16_LE) or raw.startswith(_BOM_UTF16_BE):
        return ("utf-16", True)
    if raw == b"":
        return ("utf-8", False)

    if _HAS_CN and _cn_from_bytes is not None:
        best = _cn_from_bytes(raw).best()
        if best is not None and best.encoding:
            codec = _normalize_codec(best.encoding)
            if not strict:
                return (codec, False)
            # Strict gate: low chaos + the decoded text must re-encode to the
            # exact original bytes. Catches short-input misdetection (e.g. a few
            # Cyrillic letters mislabelled big5) before we ever write.
            if best.chaos <= 0.10:
                try:
                    if raw.decode(codec).encode(codec) == raw:
                        return (codec, False)
                except (LookupError, UnicodeError):
                    pass

    # No charset-normalizer (or strict gate failed): try decoders in order and
    # take the first that decodes cleanly *and* round-trips byte-for-byte.
    for codec in _FALLBACK_CODECS:
        try:
            if raw.decode(codec).encode(codec) == raw:
                return (codec, False)
        except (LookupError, UnicodeError):
            continue
    return None


def _encoding_for_write(codec: str, had_bom: bool) -> str:
    """Map a detected read codec to the codec used to re-encode on write.

    utf-8 stays BOM-less (a BOM breaks .py/.json/.sh parsers and pollutes git
    diffs). utf-16 keeps its BOM (Python's "utf-16" codec emits one) — without it
    the file becomes unreadable. Legacy 8-bit codecs are written back unchanged.
    """
    base = _normalize_codec(codec)
    if base in ("utf_8", "utf-8", "utf8"):
        return "utf-8"
    if had_bom and base in ("utf_16", "utf-16", "utf16"):
        return "utf-16"
    return base


def _save_backup(path: str, raw_bytes: bytes) -> str | None:
    """Persist a file's pre-edit bytes so the last change is recoverable
    (single-step undo). Bounded: one backup per file path — the latest
    pre-edit snapshot, overwritten on each edit — so backups never grow
    unbounded. Best-effort: a failed backup must never block the write.
    Returns the backup file path on success, else None.
    """
    try:
        import hashlib
        from app.core.data_files import data_subdir

        key = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
        bdir = data_subdir("code_agent_backups")
        backup = bdir / f"{key}.bak"
        backup.write_bytes(raw_bytes)
        (bdir / f"{key}.path").write_text(str(path), encoding="utf-8")
        return str(backup)
    except Exception:
        return None


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
        raw = target.read_bytes()
    except Exception as exc:
        return {"text": f"ERROR: {exc}"}
    if _looks_binary(raw):
        return {"text": f"ERROR: binary file (not text): {path}"}

    detected = _detect_encoding(raw, strict=False)
    if detected is not None:
        codec, had_bom = detected
        body = raw[len(_BOM_UTF8):] if (had_bom and codec == "utf-8") else raw
        try:
            text = body.decode(codec)
        except Exception:
            # Detection said one thing but decode still failed — soft fallback.
            text = raw.decode("cp1252", errors="replace")
    else:
        # Reads degrade softly: show the file rather than refusing it.
        text = raw.decode("cp1252", errors="replace")

    text = _to_text_newlines(text)
    lines = text.splitlines(keepends=True)
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

    write_codec = "utf-8"  # new files default to UTF-8 (no BOM)
    old_content = ""
    backup_path: str | None = None
    if existed:
        try:
            raw = target.read_bytes()
        except Exception:
            raw = b""
        detected = _detect_encoding(raw, strict=True)
        if detected is None:
            # Existing file whose encoding we can't pin down: refuse rather than
            # blindly rewrite it as utf-8 and corrupt its non-ASCII content.
            return {
                "text": (
                    f"ERROR: cannot reliably detect the encoding of existing file "
                    f"{path}; refusing to overwrite it. Confirm the encoding or "
                    "remove the file first."
                )
            }
        codec, had_bom = detected
        write_codec = _encoding_for_write(codec, had_bom)
        body = raw[len(_BOM_UTF8):] if (had_bom and codec == "utf-8") else raw
        try:
            old_content = _to_text_newlines(body.decode(codec))
        except Exception:
            old_content = ""
        # Snapshot the original bytes before overwriting so the change is
        # recoverable (single-step undo).
        backup_path = _save_backup(path, raw)

    target.write_bytes(content.encode(write_codec))
    action = "Overwrote" if existed else "Created"
    return {
        "text": f"{action} {path} ({len(content)} chars)",
        "touched_path": path,
        "old_content": old_content,
        "new_content": content,
        "diff_action": "overwrite" if existed else "create",
        "backup_path": backup_path,
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
    try:
        raw = target.read_bytes()
    except Exception as exc:
        return {"text": f"ERROR: {exc}"}

    detected = _detect_encoding(raw, strict=True)
    if detected is None:
        # Can't pin down the encoding — refuse to edit so we don't corrupt the
        # rest of the file while replacing one line.
        return {
            "text": (
                f"ERROR: cannot reliably detect the encoding of {path}; refusing "
                "to edit it. Confirm the encoding or rewrite the file explicitly."
            )
        }
    codec, had_bom = detected
    write_codec = _encoding_for_write(codec, had_bom)
    body = raw[len(_BOM_UTF8):] if (had_bom and codec == "utf-8") else raw
    current = _to_text_newlines(body.decode(codec))

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
    backup_path = _save_backup(path, raw)
    target.write_bytes(updated.encode(write_codec))
    return {
        "text": f"Edited {path} (1 replacement)",
        "touched_path": path,
        "old_content": current,
        "new_content": updated,
        "diff_action": "edit",
        "backup_path": backup_path,
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


# Directories the internal grep never descends into. These are dependency,
# build, VCS and agent-runtime trees: scanning them is never what the model
# wants and they hold the huge/binary files that previously made grep hang for
# minutes while holding the global write-lock. Matched against path parts so an
