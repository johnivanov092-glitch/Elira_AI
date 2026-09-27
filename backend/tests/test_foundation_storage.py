from __future__ import annotations

from contextlib import contextmanager, closing
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


def test_publication_failure_never_exposes_partial_backup_and_retry_publishes_complete_bytes(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("foundation_storage_atomic_test", scripts / "foundation_storage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Host:
        impersonating = False

        @contextmanager
        def impersonate(self):
            self.impersonating = True
            try:
                yield
            finally:
                self.impersonating = False

    source = tmp_path / "user/snapshot"
    source.mkdir(parents=True)
    # Alphabetically the manifest precedes the database: the historical failure.
    (source / "snapshot.json").write_text(json.dumps({"databases": ["state.db"]}), encoding="utf-8")
    with closing(sqlite3.connect(source / "state.db")) as db, db:
        db.execute("CREATE TABLE state(value TEXT)")
        db.execute("INSERT INTO state VALUES('complete backup')")
    original = {p.name: p.read_bytes() for p in source.iterdir()}
    private = tmp_path / "private"
    private.mkdir()
    destination = private / "backups/selected"
    host = Host()
    storage = module.FoundationStorage(host, protected_root=private, worker_root=tmp_path / "user/work")
    fail = True

    @contextmanager
    def read_source(path, boundary):
        assert host.impersonating
        assert boundary == source
        assert not destination.exists()
        if fail and path.name == "state.db":
            raise OSError("injected failure after copying the manifest")
        with path.open("rb") as stream:
            yield stream

    monkeypatch.setattr(module, "safe_read_file", read_source)
    with pytest.raises(OSError, match="injected failure"):
        storage.publish(source, destination)
    assert not destination.exists()
    abandoned = list(destination.parent.glob(".selected.publishing-*"))
    assert len(abandoned) == 1
    assert (abandoned[0] / "snapshot.json").read_bytes() == original["snapshot.json"]
    assert not (abandoned[0] / "state.db").exists()

    rename = module.os.rename
    committed = []

    def publish_complete(staging, target):
        assert not host.impersonating
        assert {p.name: p.read_bytes() for p in staging.iterdir()} == original
        assert target == destination and not target.exists()
        rename(staging, target)
        committed.append(target)

    monkeypatch.setattr(module.os, "rename", publish_complete)
    fail = False
    storage.publish(source, destination)
    assert committed == [destination]
    assert {p.name: p.read_bytes() for p in destination.iterdir()} == original
    assert {p.name: p.read_bytes() for p in source.iterdir()} == original
    with closing(sqlite3.connect(destination / "state.db")) as db:
        assert db.execute("SELECT value FROM state").fetchone()[0] == "complete backup"
    with pytest.raises(ValueError, match="new private destination"):
        storage.publish(source, destination)
