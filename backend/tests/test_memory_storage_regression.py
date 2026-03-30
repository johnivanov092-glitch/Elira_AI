from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"


def _create_root_elira_state(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                pinned INTEGER NOT NULL DEFAULT 0,
                memory_saved INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                ollama_context INTEGER NOT NULL DEFAULT 8192,
                default_model TEXT NOT NULL DEFAULT 'gemma3:4b',
                agent_profile TEXT NOT NULL DEFAULT 'Универсальный'
            )
            """
        )
        conn.execute(
            "INSERT INTO chats (title, pinned, memory_saved) VALUES ('Root chat', 1, 0)"
        )
        conn.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (1, 'assistant', 'root message')"
        )
        conn.execute(
            """
            INSERT INTO settings (id, ollama_context, default_model, agent_profile)
            VALUES (1, 8192, 'gemma3:4b', 'Универсальный')
            """
        )
        conn.commit()
    finally:
        conn.close()


def _create_archive_elira_state(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("INSERT INTO chats (title) VALUES ('Archive chat')")
        conn.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (1, 'user', 'archive message')"
        )
        conn.commit()
    finally:
        conn.close()


def _create_seed_rag(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE rag_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                category TEXT DEFAULT 'fact',
                embedding TEXT DEFAULT '',
                importance INTEGER DEFAULT 5,
                access_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO rag_items (text, category, embedding, importance)
            VALUES ('RAG alpha memory', 'fact', '[0.1, 0.2, 0.3]', 6)
            """
        )
        conn.commit()
    finally:
        conn.close()


class MemoryStorageRegressionTest(unittest.TestCase):
    def test_root_runtime_ignores_archive_directory(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir, tempfile.TemporaryDirectory() as archive_dir:
            data_path = Path(data_dir)
            archive_path = Path(archive_dir)
            _create_root_elira_state(data_path / "elira_state.db")
            _create_archive_elira_state(archive_path / "elira_state.db")

            script = textwrap.dedent(
                f"""
                import json
                import sys
                sys.path.insert(0, r"{BACKEND_ROOT}")

                from app.services.elira_memory_sqlite import list_chats
                from app.services.runtime_service import get_runtime_status

                payload = {{
                    "chat_titles": [chat["title"] for chat in list_chats()],
                    "runtime": get_runtime_status(),
                }}
                print(json.dumps(payload, ensure_ascii=False))
                """
            )

            env = os.environ.copy()
            env["ELIRA_DATA_DIR"] = str(data_path)

            proc = subprocess.run(
                [sys.executable, "-c", script],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout.strip())

            self.assertEqual(payload["chat_titles"], ["Root chat"])
            self.assertEqual(payload["runtime"]["active_chat_count"], 1)
            self.assertEqual(payload["runtime"]["storage_mode"], "rooted_sqlite")
            self.assertEqual(payload["runtime"]["active_db_path"], str((data_path / "elira_state.db").resolve()))
            self.assertEqual(payload["runtime"]["primary_engine"], "duckduckgo")
            self.assertTrue(payload["runtime"]["degraded_mode"])
            self.assertIn("duckduckgo", payload["runtime"]["available_engines"])
            self.assertIn("wikipedia", payload["runtime"]["available_engines"])
            self.assertIsInstance(payload["runtime"]["warning"], str)

    def test_root_memory_profile_isolation_and_rag_seed_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            data_path = Path(data_dir)
            _create_seed_rag(data_path / "rag_memory.db")

            script = textwrap.dedent(
                f"""
                import json
                import sys
                sys.path.insert(0, r"{BACKEND_ROOT}")

                from fastapi.testclient import TestClient
                from app.main import app
                from app.services.rag_memory_service import get_rag_context, rag_stats

                client = TestClient(app)
                client.post("/api/memory/add", json={{"profile": "default", "text": "alpha fact", "source": "manual"}})
                client.post("/api/memory/add", json={{"profile": "other", "text": "beta fact", "source": "manual"}})

                payload = {{
                    "default_items": client.get("/api/memory/items/default").json()["count"],
                    "other_items": client.get("/api/memory/items/other").json()["count"],
                    "rag_total": rag_stats()["total"],
                    "rag_context": get_rag_context("alpha"),
                }}
                print(json.dumps(payload, ensure_ascii=False))
                """
            )

            env = os.environ.copy()
            env["ELIRA_DATA_DIR"] = str(data_path)

            proc = subprocess.run(
                [sys.executable, "-c", script],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout.strip())

            self.assertEqual(payload["default_items"], 1)
            self.assertEqual(payload["other_items"], 1)
            self.assertEqual(payload["rag_total"], 0)
            self.assertNotIn("[fact]", payload["rag_context"])
            self.assertNotIn("RAG alpha memory", payload["rag_context"])


if __name__ == "__main__":
    unittest.main()
