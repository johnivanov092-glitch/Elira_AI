"""Locate the shared pure library shipped with Elira (no backend import)."""
import os
import sys
from pathlib import Path

def configure() -> None:
    explicit = os.getenv("ELIRA_SHARED_ROOT")
    candidates = [Path(explicit)] if explicit else [p / "shared" for p in Path(__file__).resolve().parents]
    root = next((p for p in candidates if (p / "elira_common/__init__.py").is_file()), None)
    if root is None:
        raise RuntimeError("Elira shared utilities unavailable; set ELIRA_SHARED_ROOT")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
