"""Instruction file loader — merges .elira/agent.md from project scopes.

Load order (least specific → most specific):
  1. ~/.elira/agent.md          — user-global instructions
  2. <project_root>/.elira/agent.md
  3. <project_root>/.elira/agent.local.md
  4. <project_root>/<subdir>/.elira/agent.md up to working_dir

Limits:
  - 4 000 chars per file (excess truncated silently)
  - 12 000 chars total across all files

Deduplication:
  - Files with identical SHA-256 content hash are skipped so copy-pasted
    blocks don't get injected twice.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

_FILE_CHAR_LIMIT: int = 4_000
_TOTAL_CHAR_LIMIT: int = 12_000
_INSTRUCTION_FILENAME = ".elira/agent.md"
_LOCAL_INSTRUCTION_FILENAME = ".elira/agent.local.md"
_DEFAULT_PROJECT_INSTRUCTIONS = """# Project instructions

Write project-specific instructions for the local agent here.
Keep this file short, factual, and safe to quote in prompts.
"""

# (label, path_factory) — evaluated lazily so Path.home() isn't called at
# import time (helps with testability).
_SOURCES: list[tuple[str, object]] = [
    ("global",  lambda root: Path.home() / _INSTRUCTION_FILENAME),
    ("project", lambda root: root / _INSTRUCTION_FILENAME),
    ("local",   lambda root: root / _LOCAL_INSTRUCTION_FILENAME),
]


def _read_capped(path: Path, char_limit: int | None = None) -> str:
    """Read file, strip whitespace, and cap to char_limit. Returns '' on error."""
    if not path.is_file():
        return ""
    limit = _FILE_CHAR_LIMIT if char_limit is None else char_limit
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return text[:limit] if len(text) > limit else text


def _resolve_project_root(project_root: Path | str) -> Path:
    return Path(project_root).expanduser().resolve()


def _resolve_working_dir(project_root: Path, working_dir: Path | str | None) -> Path | None:
    if working_dir is None:
        return None
    raw = Path(working_dir).expanduser()
    candidate = raw if raw.is_absolute() else project_root / raw
    try:
        resolved = candidate.resolve()
    except Exception:
        return None
    if resolved.is_file():
        resolved = resolved.parent
    try:
        resolved.relative_to(project_root)
    except ValueError:
        return None
    return resolved


def _path_chain(project_root: Path, working_dir: Path | None) -> Iterable[Path]:
    if working_dir is None or working_dir == project_root:
        return []
    try:
        relative = working_dir.relative_to(project_root)
    except ValueError:
        return []

    current = project_root
    chain: list[Path] = []
    for part in relative.parts:
        current = current / part
        chain.append(current)
    return chain


def _display_source(project_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(project_root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _format_untrusted_block(
    *,
    label: str,
    path: Path,
    project_root: Path,
    content: str,
    budget: int,
) -> str:
    source = _display_source(project_root, path)
    prefix = f"[UNTRUSTED INSTRUCTIONS: {label}; source={source}]\n"
    suffix = "\n[/UNTRUSTED INSTRUCTIONS]"
    body_budget = budget - len(prefix) - len(suffix)
    if body_budget <= 0:
        return ""
    return prefix + content[:body_budget] + suffix


def _instruction_sources(project_root: Path, working_dir: Path | str | None = None) -> list[tuple[str, Path]]:
    root = _resolve_project_root(project_root)
    sources: list[tuple[str, Path]] = []
    for label, path_fn in _SOURCES:
        sources.append((label, path_fn(root)))  # type: ignore[operator]

    resolved_working_dir = _resolve_working_dir(root, working_dir)
    for directory in _path_chain(root, resolved_working_dir):
        sources.append(("workspace", directory / _INSTRUCTION_FILENAME))
    return sources


def load_instructions(project_root: Path | str, working_dir: Path | str | None = None) -> str:
    """Return merged instruction text for *project_root*.

    Reads instruction files from global, project, local, and optional
    project-relative working_dir scopes. Content is marked as untrusted with
    provenance, deduplicated by SHA-256 content hash, and capped by budget.
    Returns an empty string if none of the files exist.
    """
    root = _resolve_project_root(project_root)
    seen: set[str] = set()
    parts: list[str] = []
    total = 0

    for label, path in _instruction_sources(root, working_dir):
        content = _read_capped(path)
        if not content:
            continue

        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if h in seen:
            continue
        seen.add(h)

        separator_cost = 2 if parts else 0
        remaining = _TOTAL_CHAR_LIMIT - total - separator_cost
        if remaining <= 0:
            break

        block = _format_untrusted_block(
            label=label,
            path=path,
            project_root=root,
            content=content,
            budget=remaining,
        )
        if not block:
            break

        parts.append(block)
        total += separator_cost + len(block)

    return "\n\n".join(parts)


def init_project_instructions(
    project_root: Path | str,
    *,
    content: str | None = None,
) -> dict[str, Any]:
    """Create <project_root>/.elira/agent.md if missing.

    This is idempotent: an existing file is never overwritten.
    """
    root = _resolve_project_root(project_root)
    if not root.exists() or not root.is_dir():
        return {"ok": False, "created": False, "error": f"project_root does not exist: {root}"}

    target = root / _INSTRUCTION_FILENAME
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "created": False, "exists": True, "error": str(exc), "path": str(target)}
        return {"ok": True, "created": False, "exists": True, "content": existing, "path": str(target)}

    body = content if content is not None else _DEFAULT_PROJECT_INSTRUCTIONS
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "created": False, "exists": False, "error": str(exc), "path": str(target)}
    return {"ok": True, "created": True, "exists": True, "content": body, "path": str(target)}
