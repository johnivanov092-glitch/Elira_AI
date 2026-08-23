from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from app.application.code_agent.tools._sandbox import SandboxError, _resolve_safe

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
# Binary document types read_file extracts text from (via file_extract) instead
# of rejecting as "binary". Images/audio are NOT here — those use read_image /
# ocr_file (the vision/OCR tools).
_DOCUMENT_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".xls", ".xlsx", ".xlsm"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
# Latin→Cyrillic visual look-alikes: the local model often mangles long Cyrillic
# filenames by swapping in Latin twins (лечеbной, метаcтатическом), which makes
# an exact path miss. Fold them before fuzzy-matching so the real file is found.
_LOOKALIKE = {
    "a": "а", "b": "б", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м",
    "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
}


def _norm_name(name: str) -> str:
    """Lowercase, fold Latin look-alikes, drop spaces/punctuation — for matching a
    model-typed (often mangled) filename against real files in a directory."""
    s = (name or "").lower()
    s = "".join(_LOOKALIKE.get(ch, ch) for ch in s)
    return "".join(ch for ch in s if ch.isalnum())


def _fuzzy_find(target: Path) -> Path | None:
    """When an exact path misses, find the closest real file in its directory.
    Returns a match only when it is clearly the best (high ratio + margin over the
    runner-up), so an ambiguous guess never silently opens the wrong file."""
    d = target.parent
    if not d.is_dir():
        return None
    want = _norm_name(target.name)
    if len(want) < 4:
        return None
    best: Path | None = None
    best_r = 0.0
    second_r = 0.0
    try:
        entries = [f for f in d.iterdir() if f.is_file()]
    except Exception:
        return None
    # If the query names an extension, only consider files WITH that extension
    # (so "…раке.pdf" resolves to the .pdf, not a same-basename .docx sitting next
    # to it — which would otherwise tie and get refused as ambiguous).
    ext = target.suffix.lower()
    if ext:
        same_ext = [f for f in entries if f.suffix.lower() == ext]
        if same_ext:
            entries = same_ext
    for f in entries:
        r = difflib.SequenceMatcher(None, want, _norm_name(f.name)).ratio()
        if r > best_r:
            best, second_r, best_r = f, best_r, r
        elif r > second_r:
            second_r = r
    if best is not None and best_r >= 0.70 and (best_r - second_r) >= 0.08:
        return best
    return None


def _dir_hint(target: Path, cap: int = 8) -> str:
    d = target.parent
    if not d.is_dir():
        return ""
    try:
        names = [f.name for f in d.iterdir() if f.is_file()][:cap]
    except Exception:
        return ""
    return (" — файлы в этой папке: " + "; ".join(names)) if names else ""
_BOM_UTF16_LE = b"\xff\xfe"
_BOM_UTF16_BE = b"\xfe\xff"

# Codecs charset-normalizer reports that we normalise to a friendlier/correct
# equivalent on write. cp866 round-trips byte-identically through cp1125 but the
# library labels it cp1125; writing back as cp866 keeps the file's real codepage.
_CODEC_ALIASES = {
    "cp1125": "cp866",
}


def _reject_duplicate_project_root(project_root: Path, path: str) -> None:
    """Compatibility hook; path spelling is handled by the filesystem."""
    del project_root, path

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
    # ASCII is a strict UTF-8 subset: promote so writing new Unicode content
    # into an existing ASCII file succeeds as UTF-8 (no BOM) instead of
    # raising UnicodeEncodeError ('ascii' codec can't encode …). Existing
    # bytes are unchanged by the promotion; newlines are written as-is (LF).
    if base in ("ascii", "us_ascii", "us-ascii"):
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
    resolved_note = ""
    if not target.is_file():
        # The local model frequently mangles long non-ASCII filenames (Latin/
        # Cyrillic look-alikes, dropped syllables) → the exact path misses. Try a
        # fuzzy match against the real files in the directory before failing.
        alt = _fuzzy_find(target)
        if alt is not None:
            resolved_note = f"[имя '{path}' не найдено точно — открыл ближайшее: {alt.name}]\n"
            target = alt
        else:
            return {"ok": False, "error": "file_not_found",
                    "text": f"ERROR: not a file or does not exist: {path}{_dir_hint(target)}"}
    try:
        raw = target.read_bytes()
    except Exception as exc:
        return {"ok": False, "error": "read_failed", "text": f"ERROR: {exc}"}

    # Documents (pdf/docx/pptx/xls/xlsx): extract text via the shared file_extract
    # pipeline (pdf: pypdf→pdfplumber→OCR fallback for scans; docx/pptx/excel via
    # python-docx/pptx/openpyxl) instead of rejecting them as "binary". Without
    # this read_file refuses them and the model fumbles with run_bash+PyMuPDF/
    # PowerShell (seen live on the medical-docs run).
    if target.suffix.lower() in _DOCUMENT_EXTS:
        try:
            from app.application.file_extract.runtime import extract_file
            doc_text = str((extract_file(target.name, raw) or {}).get("text") or "")
        except Exception as exc:
            return {"ok": False, "error": "extraction_failed",
                    "text": f"ERROR: не удалось извлечь текст из {target.suffix} ({path}): {exc}"}
        if not doc_text.strip():
            return {"text": (
                f"[{target.suffix}: текст не извлечён — вероятно скан без текстового "
                f"слоя (нужен OCR) или пустой файл: {path}]"
            )}
        text = _to_text_newlines(doc_text)
        lines = text.splitlines(keepends=True)
        start = max(0, int(offset))
        end = start + max(1, int(limit))
        selected = lines[start:end]
        numbered = "".join(f"{i + 1 + start:>5}\t{ln}" for i, ln in enumerate(selected))
        suffix = "" if end >= len(lines) else f"\n[... truncated at line {end} of {len(lines)}]"
        header = f"[текст извлечён из {target.suffix} через file_extract: {target.name}]\n"
        return {"text": resolved_note + header + numbered + suffix, "touched_path": path}

    if _looks_binary(raw):
        if target.suffix.lower() in _IMAGE_EXTS:
            # Auto-OCR images so "прочитай это фото/скан" just works (like documents);
            # empty OCR (a real photo, not a document scan) → point at read_image.
            try:
                from app.application.code_agent.tools._vision import tool_ocr_file
                _ocr = str((tool_ocr_file(project_root, path=str(target)) or {}).get("text") or "").strip()
            except Exception as exc:
                _ocr = f"ERROR: OCR failed: {exc}"
            if _ocr and not _ocr.startswith("ERROR"):
                return {"text": resolved_note + f"[текст с изображения через OCR: {target.name}]\n{_ocr}", "touched_path": path}
            if _ocr.startswith("ERROR"):
                return {"text": resolved_note + f"[{target.name}: {_ocr}. Для описания картинки вызови `read_image`.]"}
            return {"text": resolved_note + (
                f"[{target.suffix} {target.name}: OCR не нашёл текста (похоже, обычное "
                f"фото, а не скан документа). Для описания изображения вызови `read_image`.]"
            )}
        return {"ok": False, "error": "binary_file",
                "text": f"ERROR: binary file (not text): {path}"}

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
        "text": resolved_note + numbered + suffix,
        "touched_path": path,
    }


def tool_write_file(project_root: Path, *, path: str, content: str) -> dict[str, Any]:
    _reject_duplicate_project_root(project_root, path)
    target = _resolve_safe(project_root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()

    write_codec = "utf-8"  # new files default to UTF-8 (no BOM)
    old_content = ""
    backup_path: str | None = None
    if existed:
        try:
            raw = target.read_bytes()
        except OSError:
            return {
                "ok": False,
                "error": "read_failed",
                "text": (
                    f"ERROR: failed to read existing file {path}; "
                    "refusing to overwrite it."
                ),
            }
        detected = _detect_encoding(raw, strict=True)
        if detected is None:
            # Existing file whose encoding we can't pin down: refuse rather than
            # blindly rewrite it as utf-8 and corrupt its non-ASCII content.
            return {
                "ok": False,
                "error": "encoding_undetected",
                "text": (
                    f"ERROR: cannot reliably detect the encoding of existing file "
                    f"{path}; refusing to overwrite it. Confirm the encoding or "
                    "remove the file first."
                ),
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

    try:
        encoded = content.encode(write_codec)
    except (UnicodeEncodeError, LookupError) as exc:
        # The new content does not fit the preserved legacy encoding (e.g. an
        # emoji into a cp1251 file). Honest failure — never a silent success.
        return {
            "ok": False,
            "error": "encoding_conflict",
            "text": (
                f"ERROR: new content cannot be encoded as {write_codec} "
                f"(preserved encoding of {path}): {exc}"
            ),
        }
    try:
        target.write_bytes(encoded)
    except OSError as exc:
        return {
            "ok": False,
            "error": "write_failed",
            "text": f"ERROR: failed to write {path}: {exc}",
        }
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
    _reject_duplicate_project_root(project_root, path)
    target = _resolve_safe(project_root, path)
    if not target.is_file():
        return {"ok": False, "error": "file_not_found",
                "text": f"ERROR: not a file or does not exist: {path}"}
    try:
        raw = target.read_bytes()
    except Exception as exc:
        return {"ok": False, "error": "read_failed", "text": f"ERROR: {exc}"}

    detected = _detect_encoding(raw, strict=True)
    if detected is None:
        # Can't pin down the encoding — refuse to edit so we don't corrupt the
        # rest of the file while replacing one line.
        return {
            "ok": False,
            "error": "encoding_undetected",
            "text": (
                f"ERROR: cannot reliably detect the encoding of {path}; refusing "
                "to edit it. Confirm the encoding or rewrite the file explicitly."
            ),
        }
    codec, had_bom = detected
    write_codec = _encoding_for_write(codec, had_bom)
    body = raw[len(_BOM_UTF8):] if (had_bom and codec == "utf-8") else raw
    current = _to_text_newlines(body.decode(codec))

    if old_string not in current:
        return {"ok": False, "error": "old_string_not_found",
                "text": f"ERROR: old_string not found in {path}"}
    occurrences = current.count(old_string)
    if occurrences > 1:
        return {
            "ok": False,
            "error": "old_string_ambiguous",
            "text": (
                f"ERROR: old_string matches {occurrences} times in {path}. "
                "Provide a larger surrounding context to make it unique."
            ),
        }
    updated = current.replace(old_string, new_string, 1)
    try:
        encoded = updated.encode(write_codec)
    except (UnicodeEncodeError, LookupError) as exc:
        return {
            "ok": False,
            "error": "encoding_conflict",
            "text": (
                f"ERROR: edited content cannot be encoded as {write_codec} "
                f"(preserved encoding of {path}): {exc}"
            ),
        }
    backup_path = _save_backup(path, raw)
    try:
        target.write_bytes(encoded)
    except OSError as exc:
        return {"ok": False, "error": "write_failed",
                "text": f"ERROR: failed to write {path}: {exc}"}
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


def tool_path_exists(project_root: Path, *, path: str) -> dict[str, Any]:
    """Deterministically check whether a LOCAL file or directory exists — the verifier
    for "создана папка X" / "файл X существует" / "проект внутри X" criteria on a local
    project (the local counterpart of ssh_exists). Read-only; resolves inside the project
    root. `ok` reflects presence, so a matching file_exists criterion confirms on
    presence and a file_not_exists (cleanup) criterion confirms on absence."""
    raw = (path or "").strip().strip("`\"'")
    if not raw:
        return {"text": "ERROR: path is empty", "ok": False}
    try:
        target = (project_root / raw)
        exists = target.exists()
        kind = "каталог" if target.is_dir() else ("файл" if target.is_file() else "")
    except Exception as exc:
        return {"text": f"ERROR: {exc}", "ok": False}
    note = f"{raw}: {('существует (' + kind + ')') if exists else 'НЕ найден'}"
    return {"text": note, "ok": exists, "verifier": True, "evidence": note}


# Directories the internal grep never descends into. These are dependency,
# build, VCS and agent-runtime trees: scanning them is never what the model
# wants and they hold the huge/binary files that previously made grep hang for
# minutes while holding the global write-lock. Matched against path parts so an
