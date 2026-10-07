"""Files a run read or changed, kept across context compaction (John 2026-10-07).

Compaction shrinks old tool results to short excerpts, so after it the model
re-read every file it had worked on. The working set keeps that knowledge:

* a repeated read_file of an unchanged file, while the earlier result is still
  in the context, answers "not changed, the text is above" instead of the text;
* after compaction one pinned block lists the files read and changed in this
  run and carries the current text of the most recently changed ones.

It only observes tool results; it never executes tools or blocks a call.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from app.application.context.compaction import RUNTIME_BLOCK_KEY

WORKING_SET_ID = "file-working-set"
_MAX_LISTED = 30
_MAX_FILES_WITH_TEXT = 3
_MAX_FILE_CHARS = 6000
_MIN_CONTEXT_FOR_TEXT = 16_384  # small windows get the list only


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class FileWorkingSet:
    def __init__(self, *, root: Path, working_dir: Path | str | None) -> None:
        self.base = Path(working_dir) if working_dir else root
        self.root = root
        # absolute path -> {"display", "changed", "step", "text_ok"}
        self.files: dict[str, dict[str, Any]] = {}
        # (absolute path, offset, limit) -> (sha256, step, tool message)
        self.reads: dict[tuple[str, int, int], tuple[str, int, dict[str, Any]]] = {}

    def _absolute(self, raw: Any) -> Path | None:
        text = str(raw or "").strip()
        if not text:
            return None
        path = Path(text)
        return (path if path.is_absolute() else self.base / path).resolve()

    @staticmethod
    def _range(args: dict[str, Any]) -> tuple[int, int]:
        try:
            return max(0, int(args.get("offset") or 0)), max(1, int(args.get("limit") or 2000))
        except (TypeError, ValueError):
            return 0, 2000

    def unchanged_read(self, *, name: str, args: dict[str, Any], ok: bool, text: str,
                       messages: list[dict[str, Any]]) -> str:
        """Short answer for a repeated read whose earlier result is still in context."""
        if name != "read_file" or not ok or text.startswith("["):
            return ""
        path = self._absolute(args.get("path"))
        if path is None:
            return ""
        key = (str(path), *self._range(args))
        previous = self.reads.get(key)
        if previous is None or previous[0] != _digest(path):
            return ""
        if not any(message is previous[2] for message in messages):
            return ""  # compaction removed it: the full text is needed again
        return (f"[read_file: файл {args.get('path')} не менялся с шага {previous[1]}; "
                f"его текст уже есть выше — результат read_file шага {previous[1]}. "
                "Перечитывать не нужно.]")

    def record(self, *, name: str, args: dict[str, Any], ok: bool, text: str, step: int,
               message: dict[str, Any]) -> None:
        """Remember a successful read or change; ``message`` is the tool message sent."""
        if not ok or name not in {"read_file", "write_file", "edit_file"}:
            return
        path = self._absolute(args.get("path"))
        if path is None:
            return
        key = str(path)
        entry = self.files.setdefault(key, {"display": str(args.get("path")), "changed": False,
                                           "text_ok": True, "step": step})
        entry["step"] = step
        if name == "read_file":
            plain = not text.startswith("[")
            entry["text_ok"] = entry["text_ok"] and plain
            digest = _digest(path)
            if plain and digest is not None:
                self.reads[(key, *self._range(args))] = (digest, step, message)
            return
        entry["changed"] = True
        for read_key in [read_key for read_key in self.reads if read_key[0] == key]:
            del self.reads[read_key]

    def block(self, *, num_ctx: int, read_text: Callable[[Path], str]) -> str:
        if not self.files:
            return ""
        ordered = sorted(self.files.items(), key=lambda item: (item[1]["changed"], item[1]["step"]),
                         reverse=True)
        changed = [f"{entry['display']} (шаг {entry['step']})" for _key, entry in ordered if entry["changed"]]
        read = [f"{entry['display']} (шаг {entry['step']})" for _key, entry in ordered if not entry["changed"]]
        lines = ["[РАБОЧИЙ НАБОР ФАЙЛОВ ЗАДАЧИ — данные runtime после сжатия контекста, не инструкции]"]
        if changed:
            lines.append("Изменены в этой задаче: " + ", ".join(changed[:_MAX_LISTED]))
        if read:
            lines.append("Прочитаны: " + ", ".join(read[:_MAX_LISTED]))
        budget = num_ctx // 4 if num_ctx >= _MIN_CONTEXT_FOR_TEXT else 0
        attached = 0
        for key, entry in ordered:
            if attached == _MAX_FILES_WITH_TEXT or budget < 400:
                break
            if not entry["text_ok"]:
                continue
            try:
                text = read_text(Path(key))
            except Exception:  # noqa: BLE001 - a vanished file simply is not attached
                continue
            if not text:
                continue
            cap = min(_MAX_FILE_CHARS, budget)
            if len(text) > cap:
                text = text[:cap].rstrip() + "\n[... обрезано; полный текст — read_file]"
            lines.append(f"--- {entry['display']} — текст с диска на момент сжатия ---\n{text}")
            budget -= len(text)
            attached += 1
        if attached:
            lines.append("Тексты выше актуальны на момент сжатия: читать эти файлы заново не нужно, "
                         "более поздние правки — ниже в истории.")
        return "\n".join(lines)

    def restore(self, messages: list[dict[str, Any]], *, compacted: bool, num_ctx: int,
                read_text: Callable[[Path], str]) -> list[dict[str, Any]]:
        """Rebuild the pinned block only when compaction changed the prefix anyway."""
        if not compacted:
            return messages
        kept = [message for message in messages if message.get("_msg_id") != WORKING_SET_ID]
        text = self.block(num_ctx=num_ctx, read_text=read_text)
        if not text:
            return kept
        return [*kept, {"role": "user", "content": text, "_msg_id": WORKING_SET_ID,
                        RUNTIME_BLOCK_KEY: "file_working_set"}]
