"""Delivery-session scripted benchmarks (spec: delivery-s1, mandatory 12-13).

Two project-shaped builds driven by a scripted model through the REAL runtime
(real registry, real files on disk, real `python …` verification subprocess):

  A. desktop CRUD/CRM: several modules, SQLite, auth/roles, README, run
     config, a test — split over 2 slices of one run_id;
  B. Telegram/service project: env.example, storage, handler modules, run
     script, README, a test — same shape.

Both assert REAL artifacts and a GREEN verification command (exit_code == 0 →
verifier-confirmed criterion), never the model's text. This doubles as the
behaviour evidence for the delivery criterion: one request → ≥2 slices → same
run_id → slice 1 makes a confirmed change → slice 2 continues the open
milestone without repeating it → verified artifacts → exactly one done.

Plus (13): the SSH deployment flow at MOCK/provider level only — subprocess is
patched, no network ever.
"""
from __future__ import annotations

import contextlib
import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import agent_loop  # noqa: E402
from app.application.code_agent.agent_loop import _CODE_AGENT_BASE_TOOLS  # noqa: E402
from app.application.code_agent.delivery_session import stream_delivery_session  # noqa: E402
from app.application.tool_providers import ToolRegistry  # noqa: E402

_FAKE_SCHEMAS = [
    {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
    for n in _CODE_AGENT_BASE_TOOLS
]

_REAL_MONOTONIC = time.monotonic


class _Clock:
    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return _REAL_MONOTONIC() + self.offset


_CLOCK = _Clock()
_SLICE_TIMEOUT_S = 120
_TIMEOUT_BUMP = _SLICE_TIMEOUT_S + 1


@contextlib.contextmanager
def _loop_env():
    _CLOCK.offset = 0.0
    with patch.object(agent_loop, "_resolve_code_route", return_value=("test-model", 32768, None)), \
         patch.object(agent_loop, "_record_code_route_metric"), \
         patch.object(agent_loop, "build_mcp_providers", return_value=[]), \
         patch.object(ToolRegistry, "collect_schemas", return_value=list(_FAKE_SCHEMAS)), \
         patch.object(agent_loop, "_server_url_alive", return_value=True), \
         patch.object(agent_loop, "_run_owned_servers", return_value=[]), \
         patch.object(agent_loop, "_stop_run_servers", return_value=[]), \
         patch.object(agent_loop.time, "monotonic", _CLOCK), \
         patch("app.application.agent_registry.sandbox.preflight_or_raise",
               return_value={"limit": {"max_execution_seconds": 600}}):
        yield


def _call(name, **args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def _final(text="готово"):
    return {"message": {"content": text, "tool_calls": []}}


class _ScriptChat:
    def __init__(self, steps, fallback=None):
        self.steps = list(steps)
        self.fallback = fallback or _final()
        self.tool_calls = 0

    def __call__(self, **kw):
        if not kw.get("tools"):
            return _final("сводка")
        idx = self.tool_calls
        self.tool_calls += 1
        entry = self.steps[idx] if idx < len(self.steps) else self.fallback
        if isinstance(entry, tuple):
            bump, response = entry
            _CLOCK.offset += bump
            return response
        return entry


def _dones(events):
    return [e for e in events if e.get("type") == "done"]


def _continuings(events):
    return [e for e in events if e.get("type") == "delivery_continuing"]


def _writes_for(events, path):
    return [e for e in events if e.get("type") == "tool_call"
            and e.get("tool") == "write_file"
            and (e.get("arguments") or {}).get("path") == path]


def _assert_delivery_behaviour(tc, events, rid, verify_command):
    """The behaviour contract shared by both benchmarks."""
    dones = _dones(events)
    conts = _continuings(events)
    tc.assertEqual(len(dones), 1, "exactly one terminal done")
    tc.assertGreaterEqual(len(conts), 1, "at least two internal slices")
    run_ids = {e.get("run_id") for e in events if e.get("run_id")}
    tc.assertEqual(run_ids, {rid}, "all slices share one run_id")
    done = dones[0]
    tc.assertEqual(done.get("completion_status"), "confirmed",
                   f"criterion must be verifier-confirmed, got: {done.get('criteria')}")
    verify_calls = [e for e in events if e.get("type") == "tool_call"
                    and e.get("tool") == "run_bash"
                    and verify_command in str((e.get("arguments") or {}).get("command"))]
    tc.assertTrue(verify_calls, "the verification command must actually run")
    tc.assertTrue(any(e.get("exit_code") == 0 for e in verify_calls),
                  "the verification command must be GREEN (exit 0)")
    return done


# ── Benchmark A: desktop CRUD/CRM ────────────────────────────────────────────

_CRM_TASK = (
    "Сделай настольное мини-CRM для клиентов.\n\n"
    "Цель:\nРабочее CRM-ядро: SQLite-хранилище, auth с ролями, README.\n\n"
    "Критерии готовности:\n1. smoke-тест `python test_app.py` проходит\n"
)

_CRM_DB = """import sqlite3

def connect(path="crm.db"):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS clients ("
        "id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT DEFAULT '')"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "id INTEGER PRIMARY KEY, login TEXT UNIQUE, role TEXT NOT NULL)"
    )
    return conn
"""

_CRM_MODELS = """ROLES = ("admin", "employee")

def can_delete(role):
    return role == "admin"

def add_user(conn, login, role):
    if role not in ROLES:
        raise ValueError("bad role: " + role)
    conn.execute("INSERT OR REPLACE INTO users(login, role) VALUES (?, ?)", (login, role))
    conn.commit()

def user_role(conn, login):
    row = conn.execute("SELECT role FROM users WHERE login = ?", (login,)).fetchone()
    return row[0] if row else None
"""

_CRM_CRUD = """def add_client(conn, name, phone=""):
    cur = conn.execute("INSERT INTO clients(name, phone) VALUES (?, ?)", (name, phone))
    conn.commit()
    return cur.lastrowid

def find_clients(conn, query):
    like = "%" + query + "%"
    return conn.execute(
        "SELECT id, name, phone FROM clients WHERE name LIKE ?", (like,)
    ).fetchall()

def delete_client(conn, client_id, role):
    from app.models import can_delete
    if not can_delete(role):
        raise PermissionError("only admin deletes")
    conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    conn.commit()
"""

_CRM_MAIN = """from app.db import connect
from app import crud, models

def main():
    conn = connect()
    models.add_user(conn, "admin", "admin")
    print("CRM ready; clients:", len(crud.find_clients(conn, "")))

if __name__ == "__main__":
    main()
"""

_CRM_TEST = """import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db import connect
from app import crud, models

def run():
    if os.path.exists("crm.db"):
        os.remove("crm.db")
    conn = connect()
    models.add_user(conn, "boss", "admin")
    models.add_user(conn, "op", "employee")
    assert models.user_role(conn, "boss") == "admin"
    cid = crud.add_client(conn, "ООО Ромашка", "+7 900 000-00-00")
    assert crud.find_clients(conn, "Ромашка")[0][0] == cid
    try:
        crud.delete_client(conn, cid, "employee")
        raise AssertionError("employee must not delete")
    except PermissionError:
        pass
    crud.delete_client(conn, cid, "admin")
    assert crud.find_clients(conn, "Ромашка") == []
    conn.close()
    os.remove("crm.db")
    print("CRM OK")

if __name__ == "__main__":
    run()
"""

_CRM_README = """# Mini-CRM

Настольное CRM-ядро: SQLite, роли admin/employee.

Запуск: `python main.py`. Тест: `python test_app.py`.
"""


class DesktopCrmBenchmarkTest(unittest.TestCase):
    def test_two_slice_crm_build_with_green_verification(self):
        rid = "dlv-bench-crm"
        chat = _ScriptChat([
            # slice 1: plan + storage/auth core
            _call("todo_update", items=[
                {"id": "m1", "text": "каркас: db/models/crud", "status": "pending", "position": 0},
                {"id": "m2", "text": "entrypoint, README, тест и проверка", "status": "pending", "position": 1},
            ]),
            _call("write_file", path="app/__init__.py", content=""),
            _call("write_file", path="app/db.py", content=_CRM_DB),
            _call("write_file", path="app/models.py", content=_CRM_MODELS),
            _call("write_file", path="app/crud.py", content=_CRM_CRUD),
            _call("todo_update", updates=[{"id": "m1", "status": "completed"}]),
            (_TIMEOUT_BUMP, _call("glob", pattern="**/*.py")),   # budget stop
            # slice 2: continue the OPEN milestone — no re-writing slice-1 files
            _call("write_file", path="main.py", content=_CRM_MAIN),
            _call("write_file", path="README.md", content=_CRM_README),
            _call("write_file", path="test_app.py", content=_CRM_TEST),
            _call("run_bash", command="python test_app.py"),
            _call("todo_update", updates=[{"id": "m2", "status": "completed"}]),
            _final("Мини-CRM готов: python test_app.py зелёный."),
        ])
        from app.application.task_planner.service import list_checklist
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = list(stream_delivery_session(
                user_message=_CRM_TASK, project_root=tmp, model="test-model",
                max_steps=30, chat_fn=chat, run_id=rid,
                execution_timeout_seconds=_SLICE_TIMEOUT_S,
                approval_wait_seconds=0, auto_remember=False,
                permission_mode="bypass",
            ))
            root = Path(tmp)
            for rel in ("app/db.py", "app/models.py", "app/crud.py",
                        "main.py", "README.md", "test_app.py"):
                self.assertTrue((root / rel).is_file(), f"missing artifact: {rel}")
            self.assertIn("SQLite", (root / "README.md").read_text(encoding="utf-8"))
        done = _assert_delivery_behaviour(self, events, rid, "python test_app.py")
        # slice 1's confirmed change is not repeated in slice 2
        self.assertEqual(len(_writes_for(events, "app/db.py")), 1)
        self.assertEqual(len(_writes_for(events, "app/crud.py")), 1)
        # milestones went through the durable checklist and ended completed
        rows = list_checklist(rid)["items"]
        self.assertEqual([r["status"] for r in rows], ["completed", "completed"])
        self.assertTrue(done["ok"])


# ── Benchmark B: Telegram/service project ────────────────────────────────────

_TG_TASK = (
    "Сделай телеграм-сервис приёма заявок (без сети в тестах).\n\n"
    "Цель:\nКаркас сервиса: конфиг из env, хранилище заявок, обработчики, "
    "run-скрипт.\n\n"
    "Критерии готовности:\n1. smoke-тест `python test_service.py` проходит\n"
)

_TG_CONFIG = """import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATA_FILE = os.getenv("DATA_FILE", "data/requests.json")

def is_configured():
    return bool(BOT_TOKEN)
"""

_TG_STORAGE = """import json
import os
import threading

_LOCK = threading.RLock()

def _path():
    import config
    return config.DATA_FILE

def load():
    with _LOCK:
        if not os.path.exists(_path()):
            return []
        with open(_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)

def add(item):
    with _LOCK:
        items = load()
        item = dict(item)
        item["id"] = len(items) + 1
        items.append(item)
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        with open(_path(), "w", encoding="utf-8") as fh:
            json.dump(items, fh, ensure_ascii=False, indent=2)
        return item
"""

_TG_HANDLERS_INIT = """from . import base  # noqa: F401
"""

_TG_HANDLERS_BASE = """import storage

def handle_start(user_id):
    return "Привет! Отправь заявку текстом."

def handle_request(user_id, text):
    saved = storage.add({"user_id": user_id, "text": text, "status": "new"})
    return "Заявка №{} принята".format(saved["id"])
"""

_TG_TEST = """import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["DATA_FILE"] = "data/test_requests.json"

import config
import storage
from handlers import base

def run():
    if os.path.exists("data/test_requests.json"):
        os.remove("data/test_requests.json")
    assert config.is_configured() is False  # no real token in tests
    assert "Привет" in base.handle_start(1)
    reply = base.handle_request(42, "нужна помощь")
    assert "№1" in reply
    items = storage.load()
    assert items[0]["user_id"] == 42 and items[0]["status"] == "new"
    os.remove("data/test_requests.json")
    print("SERVICE OK")

if __name__ == "__main__":
    run()
"""

_TG_ENV_EXAMPLE = """# Telegram service configuration
BOT_TOKEN=123456:replace-me
ADMIN_IDS=111111111
DATA_FILE=data/requests.json
"""

_TG_RUN_BAT = """@echo off
chcp 65001 >nul
if not exist .env (echo Создайте .env по образцу env.example & exit /b 1)
python main.py
"""

_TG_MAIN = """import config

def main():
    if not config.is_configured():
        print("BOT_TOKEN не задан — заполните .env по образцу env.example")
        raise SystemExit(1)
    print("service configured; polling would start here")

if __name__ == "__main__":
    main()
"""

_TG_README = """# Сервис заявок (Telegram)

Каркас: `config.py` (env), `storage.py` (JSON-хранилище), `handlers/`.

Установка: скопируйте `env.example` в `.env`, заполните BOT_TOKEN.
Запуск: `run.bat`. Тест: `python test_service.py`.
"""


class TelegramServiceBenchmarkTest(unittest.TestCase):
    def test_two_slice_service_build_with_green_verification(self):
        rid = "dlv-bench-tg"
        chat = _ScriptChat([
            # slice 1: config + storage + env example
            _call("write_file", path="config.py", content=_TG_CONFIG),
            _call("write_file", path="storage.py", content=_TG_STORAGE),
            _call("write_file", path="env.example", content=_TG_ENV_EXAMPLE),
            (_TIMEOUT_BUMP, _call("glob", pattern="*.py")),      # budget stop
            # slice 2: handlers/integration + run script + docs + verification
            _call("write_file", path="handlers/__init__.py", content=_TG_HANDLERS_INIT),
            _call("write_file", path="handlers/base.py", content=_TG_HANDLERS_BASE),
            _call("write_file", path="main.py", content=_TG_MAIN),
            _call("write_file", path="run.bat", content=_TG_RUN_BAT),
            _call("write_file", path="README.md", content=_TG_README),
            _call("write_file", path="test_service.py", content=_TG_TEST),
            _call("run_bash", command="python test_service.py"),
            _final("Сервис готов: python test_service.py зелёный."),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = list(stream_delivery_session(
                user_message=_TG_TASK, project_root=tmp, model="test-model",
                max_steps=30, chat_fn=chat, run_id=rid,
                execution_timeout_seconds=_SLICE_TIMEOUT_S,
                approval_wait_seconds=0, auto_remember=False,
                permission_mode="bypass",
            ))
            root = Path(tmp)
            for rel in ("config.py", "storage.py", "env.example", "handlers/base.py",
                        "handlers/__init__.py", "main.py", "run.bat", "README.md",
                        "test_service.py"):
                self.assertTrue((root / rel).is_file(), f"missing artifact: {rel}")
            env_text = (root / "env.example").read_text(encoding="utf-8")
            self.assertIn("BOT_TOKEN", env_text)
            self.assertNotIn("BOT_TOKEN=123456:replace-me\nBOT_TOKEN", env_text)
        _assert_delivery_behaviour(self, events, rid, "python test_service.py")
        self.assertEqual(len(_writes_for(events, "storage.py")), 1)


# ── (13) SSH deployment flow — mock/provider level ONLY, no network ──────────

def _proc(returncode=0, stdout=b"", stderr=b"") -> CompletedProcess:
    return CompletedProcess(args=["ssh"], returncode=returncode, stdout=stdout, stderr=stderr)


class SshDeployFlowMockTest(unittest.TestCase):
    """Deploy-shaped remote flow through the EXISTING SshToolProvider with
    subprocess patched: upload script → run it → health probe. Proves the
    provider seam carries a deployment without any new SSH executor and
    without touching the network."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ELIRA_DATA_DIR"] = self._tmp.name
        from app.core import data_files
        importlib.reload(data_files)
        from app.application.tool_providers import ssh_acl
        importlib.reload(ssh_acl)
        from app.application.tool_providers import ssh_provider
        importlib.reload(ssh_provider)
        self.ssh_acl = ssh_acl
        self.ssh = ssh_provider
        self.ssh_acl.set_allowed_hosts(["rpc-filebase"])

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("ELIRA_DATA_DIR", None)

    def test_deploy_flow_upload_run_health(self):
        calls = []

        def scripted_run(argv, **kw):
            calls.append(list(argv))
            joined = " ".join(str(a) for a in argv)
            if "deploy.sh" in joined and "bash" in joined:
                return _proc(0, b"deployed app v1\n")
            if "curl" in joined:
                return _proc(0, b"ok\n")
            return _proc(0, b"")

        with patch.object(self.ssh.subprocess, "run", side_effect=scripted_run):
            up = self.ssh.tool_ssh_write(
                host="rpc-filebase", path="/home/reproadmin/deploy.sh",
                content="#!/bin/sh\necho deployed app v1\n",
            )
            # tool_ssh_write has no "ok" key on success — success is the
            # absence of an ERROR text (provider contract).
            self.assertNotIn("ERROR", str(up.get("text")))
            self.assertTrue(str(up.get("text")).startswith("Wrote"), up.get("text"))
            run = self.ssh.tool_ssh_run(host="rpc-filebase", command="bash /home/reproadmin/deploy.sh")
            self.assertTrue(run["ok"])
            self.assertEqual(run["exit_code"], 0)
            self.assertIn("deployed app v1", run["text"])
            health = self.ssh.tool_ssh_run(
                host="rpc-filebase", command="curl -sf http://127.0.0.1:8080/health",
            )
            self.assertTrue(health["ok"])
            self.assertIn("ok", health["text"])
        # Every remote call went through the hardened ssh argv — never raw net.
        self.assertTrue(calls)
        for argv in calls:
            self.assertEqual(argv[0], "ssh")
            self.assertIn("BatchMode=yes", " ".join(argv))
            self.assertIn("rpc-filebase", argv)

    def test_unlisted_host_still_refused(self):
        """Approval/ACL boundary intact: a host outside the allowlist is
        refused before any subprocess call."""
        with patch.object(self.ssh.subprocess, "run") as sub:
            res = self.ssh.tool_ssh_run(host="evil-host", command="id")
        self.assertFalse(res["ok"])
        sub.assert_not_called()


if __name__ == "__main__":
    unittest.main()
