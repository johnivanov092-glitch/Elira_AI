from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.infrastructure.secrets import vault  # noqa: E402


def test_encrypted_portable_backup_allowlist_contains_both_memory_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vault, "data_file", lambda name: tmp_path / name)
    components = vault._portable_components()

    assert components["smart_memory.db"] == ("sqlite", tmp_path / "smart_memory.db")
    assert components["rag_memory.db"] == ("sqlite", tmp_path / "rag_memory.db")
    assert "web_corpus.sqlite3" not in components
