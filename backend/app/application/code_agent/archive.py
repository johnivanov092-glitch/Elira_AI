"""Export a chat folder as a ZIP archive of Markdown transcripts.

One ``.md`` file per chat of the folder: the full text of every user message
and every assistant answer with an explicit author heading. Files are UTF-8
without BOM with LF line endings, and their names are safe for Windows.
Duplicate chat titles never overwrite each other inside the archive.

The export is strictly read-only: sessions and folder state are never modified.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timezone
from typing import Any

from app.application.code_agent import sessions as session_store

_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_NAME_LEN = 100
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}


class ArchiveError(Exception):
    """Base error for folder archive export."""


class FolderNotFound(ArchiveError):
    def __init__(self, folder_id: str) -> None:
        super().__init__(f"folder not found: {folder_id}")
        self.folder_id = folder_id


class FolderEmpty(ArchiveError):
    def __init__(self, folder_id: str) -> None:
        super().__init__(f"folder has no chats: {folder_id}")
        self.folder_id = folder_id


def sanitize_windows_name(name: str, fallback: str = "Без названия") -> str:
    """Make a string safe as a Windows file name.

    Replaces characters Windows forbids in file names, drops leading/trailing
    spaces and dots (Windows strips them silently), and caps the length so the
    name plus an extension stays well under the 255-char limit.
    """
    cleaned = _FORBIDDEN_CHARS.sub("_", name)
    cleaned = cleaned.strip(" .")
    if len(cleaned) > _MAX_NAME_LEN:
        cleaned = cleaned[:_MAX_NAME_LEN].rstrip().rstrip(".")
    cleaned = cleaned or fallback
    if cleaned.split(".", 1)[0].upper() in _RESERVED_NAMES:
        cleaned = "_" + cleaned
    return cleaned


def _unique_name(base: str, taken: set[str]) -> str:
    """Return ``base`` or ``base (2)``, ``base (3)``… so it does not collide
    with already-used names. Duplicate titles must never overwrite each other."""
    candidate, suffix = base, 2
    while candidate.casefold() in taken:
        candidate = f"{base} ({suffix})"
        suffix += 1
    taken.add(candidate.casefold())
    return candidate


def _normalize_lf(text: str) -> str:
    """Normalize CRLF/CR to LF so every archive entry uses LF line endings."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _format_timestamp(ms: int | None) -> str:
    if not ms:
        return "—"
    moment = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
    return moment.strftime("%Y-%m-%d %H:%M")


def _turn_markdown(turn: dict[str, Any]) -> list[str]:
    kind = turn.get("kind")
    if kind == "user":
        return ["## 👤 Пользователь", "", _normalize_lf(str(turn.get("text") or "")), ""]
    if kind == "agent":
        return ["## 🤖 Elira", "", _normalize_lf(str(turn.get("text") or "")), ""]
    if kind == "files":
        names = ", ".join(str(f.get("name") or "?") for f in turn.get("files") or [])
        return ["## 📎 Файлы", "", names, ""]
    return []


def session_markdown(session: dict[str, Any]) -> str:
    """Render one chat as a Markdown transcript (UTF-8 text, LF endings)."""
    lines = [
        f"# {session.get('title') or 'Без названия'}",
        "",
        f"- Чат: `{session.get('id')}`",
        f"- Создан: {_format_timestamp(session.get('created_at'))}",
        f"- Обновлён: {_format_timestamp(session.get('updated_at'))}",
        "",
    ]
    for turn in session.get("turns") or []:
        if isinstance(turn, dict):
            lines.extend(_turn_markdown(turn))
    return _normalize_lf("\n".join(lines)) + "\n"


def build_folder_archive(folder_id: str) -> tuple[bytes, str, int]:
    """Build the ZIP for one folder.

    Returns ``(zip_bytes, suggested_file_name, chat_count)``. Raises
    :class:`FolderNotFound` / :class:`FolderEmpty` for unusable folders.
    Only chats assigned to this folder are included; nothing is modified.
    """
    state = session_store.get_chat_folders() or {}
    folders: list[dict[str, Any]] = state.get("folders") or []
    folder = next((item for item in folders if item.get("id") == folder_id), None)
    if folder is None:
        raise FolderNotFound(folder_id)

    assign: dict[str, str] = state.get("assign") or {}
    session_ids = [sid for sid, fid in assign.items() if fid == folder_id]
    chats = [s for s in (session_store.get_session(sid) for sid in session_ids) if s is not None]
    # Same order as the sidebar: pinned first, then most recently updated.
    chats.sort(key=lambda s: (not s.get("pinned"), -int(s.get("updated_at") or 0)))
    if not chats:
        raise FolderEmpty(folder_id)

    taken: set[str] = set()
    names = [_unique_name(sanitize_windows_name(str(s.get("title") or "")), taken) for s in chats]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for session, name in zip(chats, names):
            archive.writestr(f"{name}.md", session_markdown(session).encode("utf-8"))
    zip_name = sanitize_windows_name(str(folder.get("name") or "")) + ".zip"
    return buffer.getvalue(), zip_name, len(chats)
