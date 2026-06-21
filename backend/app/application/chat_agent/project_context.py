from __future__ import annotations

from pathlib import Path
import re

from app.application.chat_agent.project_tools import BLOCKED_DIRS, TEXT_EXTS


def ensure_chat_project_root(project_root: str | None) -> Path | None:
    raw = (project_root or "").strip()
    if not raw:
        return None

    root = Path(raw).expanduser().resolve()
    if root.exists():
        return root if root.is_dir() else None
    return None


def _is_blocked(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in BLOCKED_DIRS for part in rel.parts)


def _iter_files(root: Path, *, max_files: int) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
        if len(files) >= max_files:
            break
        if not path.is_file() or _is_blocked(path, root):
            continue
        files.append(path)
    return files


def _query_terms(query: str) -> list[str]:
    return [term for term in re.findall(r"[\w.-]{3,}", query.lower()) if len(term) <= 48][:12]


def _snippet(text: str, terms: list[str], *, limit: int = 260) -> str:
    normalized = " ".join(text.split())
    if not normalized:
        return ""
    lower = normalized.lower()
    pos = min((lower.find(term) for term in terms if term in lower), default=-1)
    if pos < 0:
        return normalized[:limit]
    start = max(0, pos - 80)
    return normalized[start : start + limit]


def build_chat_project_context(project_root: str | None, query: str, *, max_files: int = 80) -> str:
    root = ensure_chat_project_root(project_root)
    if root is None:
        return ""

    files = _iter_files(root, max_files=max_files)
    header = [
        "Selected Chat Agent project root:",
        str(root),
        "",
    ]

    if not files:
        return "\n".join(header + ["Project files: none yet."])

    rel_files = [str(path.relative_to(root)).replace("\\", "/") for path in files]
    parts = header + [
        f"Project files ({len(rel_files)} shown):",
        "\n".join(f"- {item}" for item in rel_files[:40]),
    ]

    terms = _query_terms(query)
    snippets: list[str] = []
    for path in files:
        if path.suffix.lower() not in TEXT_EXTS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        snippet = _snippet(text, terms)
        if not snippet:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        snippets.append(f"- {rel}: {snippet}")
        if len(snippets) >= 8:
            break

    if snippets:
        parts.extend(["", "Relevant file snippets:", "\n".join(snippets)])

    return "\n".join(parts)
