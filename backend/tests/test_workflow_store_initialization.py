from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from app.application.workflows import store


@pytest.mark.parametrize("legacy", [False, True])
def test_concurrent_initialization_preserves_requests_and_migrates_unique_index(tmp_path, legacy):
    database = tmp_path / "workflows.db"
    store.init_db(db_path=database)
    with sqlite3.connect(database) as connection:
        if legacy:
            connection.executescript("""
                DROP INDEX idx_workflow_requests_active_step;
                CREATE UNIQUE INDEX idx_workflow_requests_active_step
                ON workflow_requests(run_id, step_id) WHERE status IN ('pending', 'resolving');
            """)
        connection.execute("""
            INSERT INTO workflow_requests (id, request_id, workflow_id, run_id, step_id, kind, status, created_at, updated_at)
            VALUES (1, 'original', 'workflow', 'run', 'step', 'confirmation', 'pending', 'before', 'before')
        """)
    barrier = threading.Barrier(8)

    def initialize():
        barrier.wait(timeout=10)
        for _ in range(8):
            store.init_db(db_path=database)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: initialize(), range(8)))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id, status, created_at FROM workflow_requests").fetchall() == [
            (1, "pending", "before")
        ]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("""
                INSERT INTO workflow_requests (id, request_id, workflow_id, run_id, step_id, kind, status, created_at, updated_at)
                VALUES (2, 'duplicate', 'workflow', 'run', 'step', 'confirmation', 'needs_reconciliation', 'after', 'after')
            """)
