"""Prefer the project's native PDF tools over unrelated process PATH entries."""
from __future__ import annotations

import os

from app.core.config import ROOT_DIR


def poppler_options() -> dict[str, str]:
    binary_dir = ROOT_DIR / ".runtime" / "poppler" / "Library" / "bin"
    if not binary_dir.exists():
        # Preserve system-package installations on other machines, including Linux.
        return {}
    suffix = ".exe" if os.name == "nt" else ""
    for name in ("pdfinfo", "pdftoppm"):
        if not (binary_dir / (name + suffix)).is_file():
            raise RuntimeError(f"Incomplete project Poppler installation: {binary_dir}")
    return {"poppler_path": str(binary_dir)}
