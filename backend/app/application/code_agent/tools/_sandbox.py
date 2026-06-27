from __future__ import annotations

from pathlib import Path


class SandboxError(Exception):
    """Raised when a tool tries to access a path outside the project root."""


def _resolve_safe(project_root: Path, raw_path: str) -> Path:
    """Resolve `raw_path` (absolute or relative to project_root) and confirm
    it stays inside project_root. Raises SandboxError otherwise.
    """
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    resolved = candidate.resolve()
    root_resolved = project_root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SandboxError(
            f"Path '{raw_path}' resolves to {resolved}, which is outside the "
            f"project root {root_resolved}"
        ) from exc
    return resolved


def _truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    budget = max(400, limit - 100)
    head_size = int(budget * 0.65)
    tail_size = budget - head_size
    removed = len(text) - head_size - tail_size
    return (
        text[:head_size]
        + f"\n[... truncated {removed} chars from middle ...]\n"
        + text[-tail_size:]
    )
