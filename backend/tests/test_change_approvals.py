"""Dedicated-bot callback handler: strict from.id AND chat.id allowlist, one-time
capability consume + server-side apply, and the raw token never leaking into results/logs."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.change_executor import approvals, engine, store as cs, transport  # noqa: E402

_PROP = {"id": "Id", "load_state": "LoadState", "active_state": "ActiveState",
         "sub_state": "SubState", "main_pid": "MainPID"}


def F(pid, unit="netdata.service"):
    return {"id": unit, "load_state": "loaded", "active_state": "active",
            "sub_state": "running", "main_pid": pid}


def _show(fields):
    return ("\n".join(f"{_PROP[k]}={v}" for k, v in fields.items() if k in _PROP) + "\n").encode()


class FakeRunner:
    def __init__(self, inspects, apply_result=("exit", 0)):
        self.inspects, self.apply_result = list(inspects), apply_result
        self.n = 0

    def __call__(self, argv, timeout):
        if "restart" in argv:
            r = self.apply_result
            if r[0] == "timeout":
                raise subprocess.TimeoutExpired(argv, timeout)
            return transport.SshResult(r[1], b"", b"")
        self.n += 1
        spec = self.inspects[min(self.n - 1, len(self.inspects) - 1)]
        return transport.SshResult(0, _show(spec[1]), b"")


def _cb(from_id, chat_id, data):
    return {"callback_query": {"from": {"id": from_id}, "message": {"chat": {"id": chat_id}}, "data": data}}


class HandlerTest(unittest.TestCase):
    USERS, CHATS = {42}, {100}

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        cs._DB_PATH_OVERRIDE = str(Path(self._tmp) / "change.sqlite3")
        cs.init_db()
        self.reg = str(Path(self._tmp) / "registry.json")
        Path(self.reg).write_text(json.dumps({"targets": {"ai-server-netdata": {
            "host": "h", "port": 22, "remote_user": "elira-change", "known_hosts": "/kh",
            "identity_file": "/id", "unit": "netdata.service", "operation": "restart"}}}), encoding="utf-8")
        # real clock: the handler consumes capabilities against real time, so the approval
        # window must be in the real future.
        self.plan = engine.plan("ai-server-netdata", registry_path=self.reg,
                                runner=FakeRunner([("ok", F(1238))]))
        self.cid = self.plan["change_run_id"]

    def tearDown(self):
        cs._DB_PATH_OVERRIDE = None

    def _handle(self, upd, apply_runner=None):
        return approvals.handle_callback(upd, registry_path=self.reg, apply_runner=apply_runner,
                                         user_ids=self.USERS, chat_ids=self.CHATS)

    def test_non_callback_update_ignored(self):
        # a plain message update (not callback_query) must be ignored, no state change
        out = approvals.handle_callback({"message": {"text": "/approve"}},
                                        registry_path=self.reg, user_ids=self.USERS, chat_ids=self.CHATS)
        self.assertEqual(out["text"], "ignored")
        self.assertEqual(cs.get_change_run(self.cid)["change_run_status"], "pending_approval")

    def test_wrong_user_denied(self):
        out = self._handle(_cb(999, 100, self.plan["approve_token"]))
        self.assertFalse(out["ok"])
        self.assertEqual(out["text"], "not authorized")
        self.assertEqual(cs.get_change_run(self.cid)["change_run_status"], "pending_approval")

    def test_wrong_chat_denied(self):
        out = self._handle(_cb(42, 999, self.plan["approve_token"]))
        self.assertEqual(out["text"], "not authorized")
        self.assertEqual(cs.get_change_run(self.cid)["change_run_status"], "pending_approval")

    def test_empty_allowlist_denies(self):
        out = approvals.handle_callback(_cb(42, 100, self.plan["approve_token"]),
                                        registry_path=self.reg, user_ids=set(), chat_ids=set())
        self.assertEqual(out["text"], "not authorized")

    def test_invalid_token_is_noop(self):
        out = self._handle(_cb(42, 100, "garbage-token"))
        self.assertFalse(out["ok"])
        self.assertEqual(cs.get_change_run(self.cid)["change_run_status"], "pending_approval")

    def test_approve_applies_and_token_not_in_result(self):
        runner = FakeRunner([("ok", F(1238)), ("ok", F(1240))], ("exit", 0))
        out = self._handle(_cb(42, 100, self.plan["approve_token"]), apply_runner=runner)
        self.assertEqual(out["status"], "applied")
        self.assertTrue(out["ok"])
        self.assertEqual(cs.get_change_run(self.cid)["change_run_status"], "applied")
        self.assertNotIn(self.plan["approve_token"], json.dumps(out))    # raw token never returned

    def test_reject(self):
        out = self._handle(_cb(42, 100, self.plan["reject_token"]))
        self.assertEqual(out["status"], "rejected")
        self.assertEqual(cs.get_change_run(self.cid)["change_run_status"], "rejected")

    def test_malformed_env_allowlist_denies_all(self):
        # a malformed user-id allowlist ("42,abc") must deny ALL, not partially allow 42.
        with unittest.mock.patch.dict("os.environ", {
                "ITOPS_CHANGE_APPROVER_USER_IDS": "42,abc",
                "ITOPS_CHANGE_APPROVER_CHAT_IDS": "100"}):
            out = approvals.handle_callback(_cb(42, 100, self.plan["approve_token"]),
                                            registry_path=self.reg)   # uses env allowlists
        self.assertEqual(out["text"], "not authorized")
        self.assertEqual(cs.get_change_run(self.cid)["change_run_status"], "pending_approval")

    def test_replay_after_apply_is_noop(self):
        runner = FakeRunner([("ok", F(1238)), ("ok", F(1240))], ("exit", 0))
        self._handle(_cb(42, 100, self.plan["approve_token"]), apply_runner=runner)
        out = self._handle(_cb(42, 100, self.plan["approve_token"]), apply_runner=runner)
        self.assertFalse(out["ok"])                                     # capability already used
        self.assertEqual(out["text"], "expired or already handled")


if __name__ == "__main__":
    unittest.main()
