#!/usr/bin/env python3
"""Isolated UI/controller contracts; no LLM, live data, or external delivery.

The three former runtime_control roundtrips now use the existing UI routes and
typed tools. Telegram's external transport and polling body are deterministic
stubs; a PASS never certifies Telegram delivery or a live model's routing.
"""
from __future__ import annotations

import argparse
import atexit
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from typing import Any, Iterator
from unittest.mock import patch

from memory_eval import _require


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
CASE_IDS = ("vault_restore", "library_roundtrip", "telegram_typed_roundtrip")
_PASSPHRASE = "isolated-controller-eval-passphrase"
_LIBRARY_MARKER = "LIBRARY_HARNESS_CANARY_20260826"
_TELEGRAM_MARKER = "TELEGRAM_HARNESS_CANARY_20260826"


def _api(client: Any, method: str, path: str, *, status: int = 200, **kwargs: Any) -> dict[str, Any]:
    response = client.request(method, path, **kwargs)
    _require(response.status_code == status, f"{method} {path}: unexpected HTTP {response.status_code}")
    payload = response.json()
    _require(isinstance(payload, dict), f"{method} {path}: expected object response")
    return payload


@contextmanager
def _case_stores(data_dir: Path) -> Iterator[None]:
    """Restore every module override before the owner removes the temporary root."""
    from app.application.library import runtime as library
    from app.application.skill_services import telegram_runtime
    from app.application.telegram import store as telegram_store
    from app.core import data_files
    from app.infrastructure.it_ops import store as it_ops_store
    from app.infrastructure.secrets import vault

    data_dir.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        for module, name, value in (
            (data_files, "DATA_DIR", data_dir),
            (vault, "_VAULT_PATH_OVERRIDE", str(data_dir / "portable_vault.json")),
            (it_ops_store, "_DB_PATH_OVERRIDE", str(data_dir / "it_ops.sqlite3")),
            (library, "SQLITE_DB", data_dir / "library.db"),
            (library, "UPLOADS_DIR", data_dir / "uploads"),
            (telegram_store, "DB_PATH", data_dir / "integrations.db"),
            (telegram_runtime, "_running", False),
            (telegram_runtime, "_bot_thread", None),
        ):
            stack.enter_context(patch.object(module, name, value))
        try:
            vault.lock()
            library.init_library_db()
            telegram_store.init_telegram_db()
            yield
        finally:
            try:
                telegram_runtime.stop_telegram_bot()
                state = telegram_runtime.telegram_bot_status()
            finally:
                vault.lock()
            _require(not state["running"] and not state["stopping"], "Telegram receiver survived cleanup")


def _vault_restore(client: Any, data_dir: Path) -> str:
    from app.infrastructure.secrets import vault

    prefix = "/api/agent-os/vault"
    initial = _api(client, "GET", prefix + "/status")
    _require(not initial["initialized"] and initial["locked"], "vault did not start empty and locked")
    _api(client, "POST", prefix + "/create", json={"passphrase": _PASSPHRASE})
    canary = "CONTROLLER_VAULT_SECRET_CANARY_20261008"
    created = _api(client, "POST", prefix + "/secrets", json={"kind": "token", "value": canary})
    secret_ref = created["secret_ref"]
    _require(str(secret_ref).startswith("sref_"), "vault did not return an opaque secret reference")
    backup_path = data_dir / "project" / "vault-backup.elira-vault"
    backed_up = _api(client, "POST", prefix + "/backup", json={"path": str(backup_path)})
    _require(backed_up.get("ok") and backup_path.is_file(), "vault backup was not written")
    backup_text = backup_path.read_text(encoding="utf-8")
    _require(canary not in backup_text and _PASSPHRASE not in backup_text, "vault backup contains plaintext")
    late = _api(client, "POST", prefix + "/secrets", json={"kind": "token", "value": "post-backup-canary"})
    _require(_api(client, "GET", prefix + "/status")["record_count"] == 2, "post-backup mutation was not made")

    malformed = json.loads(backup_text)
    malformed["vault_sha256"] = "0" * 64
    malformed_path = data_dir / "project" / "tampered.elira-vault"
    malformed_path.write_text(json.dumps(malformed), encoding="utf-8", newline="\n")
    _api(client, "POST", prefix + "/restore", status=409, json={"path": str(malformed_path)})
    unchanged = _api(client, "GET", prefix + "/status")
    _require(unchanged["record_count"] == 2 and not unchanged["locked"], "rejected restore changed the vault")

    staged = _api(client, "POST", prefix + "/restore", json={"path": str(backup_path)})
    _require(staged["locked"] and staged["pending_state_restore"], "restore was not staged behind unlock")
    try:
        vault.resolve(secret_ref)
    except vault.VaultLocked:
        pass
    else:
        raise AssertionError("staged vault secret was accessible before unlock")
    restored = _api(client, "POST", prefix + "/unlock", json={"method": "passphrase", "credential": _PASSPHRASE})
    _require(not restored["locked"] and not restored["pending_state_restore"], "unlock did not apply staged restore")
    _require(restored["record_count"] == 1, "restore did not revert the post-backup record")
    _require(vault.resolve(secret_ref) == canary, "restored secret reference did not resolve correctly")
    try:
        vault.resolve(late["secret_ref"])
    except vault.SecretUnavailable:
        pass
    else:
        raise AssertionError("post-backup secret survived restore")
    _api(client, "POST", prefix + "/lock")
    return "UI backup/restore staged until unlock; original secret_ref restored; mutation rolled back; tamper rejected"


def _library_roundtrip(client: Any, data_dir: Path) -> str:
    from app.application.code_agent.tools._memory import tool_library
    from app.application.library import runtime as library

    file_id: int | None = None
    stored_path: Path | None = None
    canary = f"{_LIBRARY_MARKER}=violet-731"
    body = "Library pagination filler.\n" * 350 + canary + "\n"
    try:
        added = _api(client, "POST", "/api/lib/add", files={"file": ("library_canary.txt", body.encode("utf-8"), "text/plain")}, data={"use_in_context": "false"})
        _require(added.get("ok") and added.get("status") == "ready", "Library upload was not ready")
        file_id = int(added["id"])
        listed = _api(client, "GET", "/api/lib/list")
        row = next((item for item in listed["items"] if item["id"] == file_id), None)
        _require(row is not None, "uploaded Library row was not listed")
        connection = library._conn()
        try:
            stored_row = connection.execute("SELECT stored_path FROM files WHERE id = ?", (file_id,)).fetchone()
            stored_path = Path(stored_row["stored_path"])
        finally:
            connection.close()
        _require(stored_path.is_file() and stored_path.parent == data_dir / "uploads", "Library upload escaped isolated storage")
        found = tool_library(action="search", query=_LIBRARY_MARKER)
        _require(found.get("ok") and found.get("items") == 1, "typed Library search did not find the canary")
        found_id = re.search(r"\bid=(\d+)\b", found.get("text", ""))
        _require(found_id is not None and int(found_id.group(1)) == file_id, "Library search returned the wrong document id")
        read = tool_library(action="read", id=int(found_id.group(1)))
        _require(read.get("ok") and canary not in read.get("text", ""), "Library first page was not bounded")
        continuation = re.search(r"продолжение: offset=(\d+)", read.get("text", ""))
        _require(continuation is not None, "Library did not expose the continuation offset")
        tail = tool_library(action="read", id=file_id, offset=int(continuation.group(1)))
        _require(tail.get("ok") and canary in tail.get("text", ""), "typed Library paged read lost the exact canary")
    finally:
        if file_id is not None:
            deleted = _api(client, "DELETE", f"/api/lib/{file_id}")
            _require(deleted.get("ok") and deleted.get("deleted_id") == file_id, "Library delete was not accepted")
            listed_after = _api(client, "GET", "/api/lib/list")
            _require(not any(item["id"] == file_id for item in listed_after["items"]), "Library row survived cleanup")
            _require(stored_path is None or not stored_path.exists(), "Library stored file survived cleanup")
            missing = tool_library(action="read", id=file_id)
            _require(not missing.get("ok") and missing.get("error") == "file_not_found", "deleted Library document remained readable")
    return "UI upload -> typed search/bounded paged read exact canary -> UI delete; row/file absent and deleted read rejected"


def _telegram_typed_roundtrip(client: Any, data_dir: Path) -> str:
    from app.application.skill_services.telegram_actions import tool_telegram
    from app.application.skill_services import telegram_runtime
    from app.application.telegram import store as telegram_store
    from app.infrastructure.secrets import vault

    _api(client, "POST", "/api/agent-os/vault/create", json={"passphrase": _PASSPHRASE})
    token = "100200300:CONTROLLER_FAKE_TOKEN_NEVER_SENT"
    created = _api(client, "POST", "/api/agent-os/vault/secrets", json={"kind": "token", "value": token})
    configured = _api(client, "POST", "/api/telegram/config", json={"bot_token_ref": created["secret_ref"]})
    _require(configured["has_token"] and token not in json.dumps(configured), "Telegram UI leaked the token or missed configuration")
    telegram_store.set_config_value("allowed_users", "whitelist")
    chat_id = 100200300
    telegram_store.register_user(chat_id, username="controller_eval", first_name="Controller")
    _api(client, "POST", f"/api/telegram/users/{chat_id}", json={"allowed": True})
    users = _api(client, "GET", "/api/telegram/users")
    allowed = [user for user in users["users"] if user["chat_id"] == chat_id and user["allowed"]]
    _require(len(allowed) == 1, "Telegram UI did not select an allowed test user")
    calls: list[str] = []
    reject_send = False
    polling_started = threading.Event()
    polling_finished = threading.Event()

    def transport(method: str, resolved_token: str, data: dict[str, Any] | None = None, *, timeout: int = 60) -> dict[str, Any]:
        _require(resolved_token == token, "Telegram runtime did not resolve the actual vault token")
        calls.append(method)
        if method == "getMe":
            return {"ok": True, "result": {"id": 99, "username": "controller_eval_bot", "first_name": "Controller"}}
        _require(method == "sendMessage", "unexpected external Telegram operation")
        _require(data is not None and data.get("chat_id") == chat_id and data.get("text") == _TELEGRAM_MARKER, "Telegram send payload changed")
        if reject_send:
            return {"ok": False, "description": "controller transport rejected send"}
        return {"ok": True, "result": {"message_id": 77, "date": 1234}}

    def polling_body() -> None:
        polling_started.set()
        try:
            while telegram_runtime._running:
                polling_finished.wait(0.01)
        finally:
            polling_finished.set()

    with patch.object(telegram_runtime, "tg_request", transport), patch.object(telegram_runtime, "poll_loop", polling_body):
        try:
            started = _api(client, "POST", "/api/telegram/start")
            _require(started["running"] and polling_started.wait(1), "Telegram receiver did not start")
            sent = tool_telegram(action="send", chat_id=allowed[0]["chat_id"], text=_TELEGRAM_MARKER)
            receipt = sent.get("result") or {}
            _require(sent.get("ok") and receipt.get("ok") and receipt.get("message_id") == 77 and receipt.get("chat_id") == chat_id and receipt.get("date") == 1234, "typed Telegram send lost successful evidence")
            messages = tool_telegram(action="messages", chat_id=chat_id, limit=20)
            raw_log = telegram_store.get_telegram_log(chat_id=chat_id)
            _require(messages.get("ok") and _TELEGRAM_MARKER in messages.get("text", ""), "typed Telegram messages did not expose the canary")
            _require(messages.get("result") == raw_log, "typed Telegram messages diverged from the actual journal")
            _require(raw_log["count"] == 1 and raw_log["log"][0]["direction"] == "out" and raw_log["log"][0]["text"] == _TELEGRAM_MARKER, "Telegram did not write the exact successful out-log")
            reject_send = True
            failed = tool_telegram(action="send", chat_id=chat_id, text=_TELEGRAM_MARKER)
            _require(not failed.get("ok"), "failed Telegram transport was reported as success")
            _require(telegram_store.get_telegram_log(chat_id=chat_id)["count"] == 1, "failed Telegram send wrote a success log")
            before_locked = len(calls)
            vault.lock()
            locked = tool_telegram(action="send", chat_id=chat_id, text=_TELEGRAM_MARKER)
            _require(not locked.get("ok") and len(calls) == before_locked, "locked vault reached Telegram transport")
            _require(telegram_store.get_telegram_log(chat_id=chat_id)["count"] == 1, "locked vault send wrote a success log")
        finally:
            stopped = _api(client, "POST", "/api/telegram/stop")
            _require(not stopped["running"] and not stopped["stopping"] and (not polling_started.is_set() or polling_finished.is_set()), "Telegram receiver did not stop")
    _require(calls == ["getMe", "sendMessage", "sendMessage", "sendMessage"], "Telegram transport/fallback sequence changed")
    return "UI vault/config/users/start -> typed send/messages and real journal -> stop; failed/locked sends do not log success; transport stub only"


def run_controller_contracts(data_dir: Path) -> dict[str, Any]:
    """Run only in a fresh process so import-time paths cannot bind live data."""
    if "app.core.config" in sys.modules:
        raise RuntimeError("controller contracts require a fresh process before app imports")
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    _require(not any(data_dir.iterdir()), "controller contracts require an empty temporary data root")
    os.environ["ELIRA_DATA_DIR"] = str(data_dir)
    os.environ["LLAMA_SERVER_ENABLED"] = "false"
    os.environ["LOCAL_EMBED_ENABLED"] = "false"
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    cases: dict[str, dict[str, Any]] = {}
    with patch("socket.create_connection", side_effect=AssertionError("external connection forbidden")), patch("requests.sessions.Session.request", side_effect=AssertionError("external HTTP request forbidden")):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes import library_sqlite, telegram_routes, workflow_routes

        app = FastAPI()
        for router in (workflow_routes.router, library_sqlite.router, telegram_routes.router):
            app.include_router(router)
        # Windows creates a local socketpair while starting the ASGI portal.
        # Block raw connects after that bootstrap; HTTP is blocked throughout.
        with TestClient(app) as client, patch("socket.socket.connect", side_effect=AssertionError("external socket connection forbidden")):
            for name, callback in zip(CASE_IDS, (_vault_restore, _library_roundtrip, _telegram_typed_roundtrip), strict=True):
                try:
                    with _case_stores(data_dir / name):
                        details = callback(client, data_dir / name)
                    cases[name] = {"status": "PASS", "details": details}
                except Exception as exc:  # a failing contract must remain visible in the report
                    cases[name] = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
                cases[name].update({"kind": "controller_contract", "external_delivery_verified": False})
    passed = sum(case["status"] == "PASS" for case in cases.values())
    return {"ok": passed == len(CASE_IDS), "passed": passed, "total": len(CASE_IDS), "cases": cases, "kind": "controller_contract", "external_delivery_verified": False, "network": "blocked; Telegram transport and polling body stubbed"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="Explicit report directory")
    args = parser.parse_args(argv)
    suite_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir.resolve() if args.output_dir else REPO_ROOT / ".agent" / "evals" / "controllers" / suite_id
    temporary_data = tempfile.TemporaryDirectory(prefix="elira-controller-eval-")
    # App exit handlers may inspect data_file(), which recreates its directory.
    # Register first so this owner cleans again after those later handlers.
    atexit.register(temporary_data.cleanup)
    with temporary_data as temp_dir:
        data_dir = Path(temp_dir) / "data"
        try:
            result = run_controller_contracts(data_dir)
        except Exception as exc:
            result = {"ok": False, "passed": 0, "total": len(CASE_IDS), "kind": "controller_contract", "external_delivery_verified": False, "cases": {name: {"status": "FAIL", "kind": "controller_contract", "external_delivery_verified": False, "error": f"bootstrap {type(exc).__name__}: {exc}"} for name in CASE_IDS}}
    report = {"suite_id": suite_id, "created_at": datetime.now(timezone.utc).isoformat(), "data_root": str(data_dir), "isolated_data_removed": not data_dir.exists(), **result}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    lines = ["# Controller contracts", "", f"- Result: {report['passed']}/{report['total']}", "- Fresh temporary ELIRA_DATA_DIR; no live model or external network", "- Telegram transport/polling stubbed; external_delivery_verified=false", "", "| Case | Status | Details |", "|---|---|---|"]
    for name, case in report["cases"].items():
        details = str(case.get("details") or case.get("error") or "").replace("|", "\\|")
        lines.append(f"| `{name}` | {case['status']} | {details} |")
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": report["ok"], "passed": report["passed"], "total": report["total"], "kind": "controller_contract", "external_delivery_verified": False, "report": str(output_dir / "report.md")}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
