"""Instruction file loader — merges .elira/agent.md from three scopes.

Load order (global → project → local):
  1. ~/.elira/agent.md          — user-global instructions
  2. <project_root>/.elira/agent.md   — project-specific instructions
  3. <project_root>/.elira/agent.local.md — local overrides (gitignored)

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

_FILE_CHAR_LIMIT: int = 4_000
_TOTAL_CHAR_LIMIT: int = 12_000

# (label, path_factory) — evaluated lazily so Path.home() isn't called at
# import time (helps with testability).
_SOURCES: list[tuple[str, object]] = [
    ("global",  lambda root: Path.home() / ".elira" / "agent.md"),
    ("project", lambda root: root / ".elira" / "agent.md"),
    ("local",   lambda root: root / ".elira" / "agent.local.md"),
]


def _read_capped(path: Path, char_limit: int = _FILE_CHAR_LIMIT) -> str:
    """Read file, strip whitespace, and cap to char_limit. Returns '' on error."""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return text[:char_limit] if len(text) > char_limit else text


def load_instructions(project_root: Path) -> str:
    """Return merged instruction text for *project_root*.

    Reads up to three instruction files, deduplicates by content hash,
    and enforces the per-file (4 000) and total (12 000) char limits.
    Returns an empty string if none of the files exist.
    """
    seen: set[str] = set()
    parts: list[str] = []
    total = 0

    for _label, path_fn in _SOURCES:
        path: Path = path_fn(project_root)  # type: ignore[operator]
        content = _read_capped(path)
        if not content:
            continue

        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if h in seen:
            continue
        seen.add(h)

        remaining = _TOTAL_CHAR_LIMIT - total
        if remaining <= 0:
            break

        if len(content) > remaining:
            content = content[:remaining]

        parts.append(content)
        total += len(content)

    return "\n\n".join(parts)
