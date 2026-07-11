"""The two-call IPC boundary: request_plan returns ONLY {change_run_id, status} (never a
token/argv/key/binding); get_status is a tight capped whitelist (no raw output / service
paths); plan is rate-limited and fails closed when the bot/allowlist is not configured."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.change_executor import engine, ipc, store as cs, transport  # noqa: E402

_PROP = {"id": "Id", "load_state": "LoadState", "active_state": "ActiveState",
         "sub_state": "SubState", "main_pid": "MainPID"}


def F(pid, active=True):
    return {"id": "netdata.service", "load_state": "loaded",
            "active_state": "active" if active else "inactive",
            "sub_state": "running" if active else "dead", "main_pid": pid}


def _show(fields):
    return ("\n".join(f"{_PROP[k]}={v}" for k, v in fields.items() if k in _PROP) + "\n").encode()


class FakeRunner:
    def __init__(self, inspects):
        self.inspects, self.n = list(inspects), 0

    def __call__(self, argv, timeout):
        self.n += 1
        spec = self.inspects[min(self.n - 1, len(self.inspects) - 1)]
        return transport.SshResult(0, _show(spec[1]), b"")


class FakeSender:
    def __init__(self, configured=True):
        self.configured, self.sent = configured, []

    def is_configured(self):
        return self.configured

    def send_approval(self, *, change_run_id, target_id, snapshot, approve_token, reject_token):
        self.sent.append({"change_run_id": change_run_id, "approve_token": approve_token,
                          "reject_token": reject_token})


class IpcTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        cs._DB_PATH_OVERRIDE = str(Path(self._tmp) / "change.sqlite3")
        cs.init_db()
        self.reg = str(Path(self._tmp) / "registry.json")
        Path(self.reg).write_text(json.dumps({"targets": {"ai-server-netdata": {
            "host": "h", "port": 22, "remote_user": "elira-change", "known_hosts": "/kh",
            "identity_file": "/id", "unit": "netdata.service", "operation": "restart"}}}), encoding="utf-8")

    def tearDown(self):
        cs._DB_PATH_OVERRIDE = None

    def _count(self):
        conn = cs._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM change_runs").fetchone()[0]
        finally:
            conn.close()

    def _req(self, sender, **kw):
        return ipc.request_plan("ai-server-netdata", sender=sender, registry_path=self.reg,
                                runner=FakeRunner([("ok", F(1238))]), **kw)

    def test_request_plan_returns_only_id_and_status(self):
        s = FakeSender()
        out = self._req(s)
        self.assertEqual(set(out.keys()), {"ok", "change_run_id", "status"})
        self.assertEqual(out["status"], "pending_approval")
        blob = json.dumps(out)
        for leak in ("token", "argv", "binding", "known_hosts", "sudo", "systemctl", "identity"):
            self.assertNotIn(leak, blob)
        # the tokens went to the sender (executor-internal), not the IPC result
        self.assertEqual(len(s.sent), 1)
        self.assertTrue(s.sent[0]["approve_token"] and s.sent[0]["reject_token"])

    def test_fail_closed_when_sender_not_configured(self):
        out = self._req(FakeSender(configured=False))
        self.assertEqual(out["error"], "not_configured")
        self.assertEqual(self._count(), 0)                 # NO ChangeRun created, no send

    def test_rate_limited_before_planning(self):
        s = FakeSender()
        lim = ipc.RateLimiter(max_calls=1, window_seconds=1000.0, clock=lambda: 0.0)
        self.assertTrue(self._req(s, rate_limiter=lim)["ok"])
        out = self._req(s, rate_limiter=lim)
        self.assertEqual(out["error"], "rate_limited")

    def test_precondition_not_met(self):
        out = ipc.request_plan("ai-server-netdata", sender=FakeSender(), registry_path=self.reg,
                               runner=FakeRunner([("ok", F(1238, active=False))]))
        self.assertEqual(out["error"], "precondition_not_met")

    def test_unknown_target(self):
        out = ipc.request_plan("nope", sender=FakeSender(), registry_path=self.reg,
                               runner=FakeRunner([("ok", F(1238))]))
        self.assertEqual(out["error"], "unknown_or_invalid_target")

    def test_get_status_is_capped_whitelist(self):
        s = FakeSender()
        cid = self._req(s)["change_run_id"]
        cs.record_change_evidence(
            change_run_id=cid, target_id="ai-server-netdata", operation="systemd_change:postcheck",
            result=json.dumps({"verdict": "applied", "reason": "x" * 500, "stderr": "SECRETLOG",
                               "fields": {"id": "netdata.service", "load_state": "loaded",
                                          "active_state": "active", "sub_state": "running",
                                          "main_pid": 1240, "unit_file_state": "enabled",
                                          "exec_main_status": 0, "n_restarts": 1,
                                          "fragment_path": "/etc/secret/override.conf"}}),
            exit_status="0")
        out = ipc.get_status(cid)
        blob = json.dumps(out)
        for leak in ("SECRETLOG", "fragment_path", "/etc/secret", "planned_binding",
                     "planned_argv", "snapshot", "approver"):
            self.assertNotIn(leak, blob)
        res = out["evidence"][0]["result"]
        self.assertEqual(set(res["fields"].keys()),
                         {"id", "load_state", "active_state", "sub_state", "unit_file_state",
                          "main_pid", "exec_main_status", "n_restarts"})     # no fragment_path
        self.assertLessEqual(len(res["reason"]), 200)                        # capped
        self.assertEqual(out["status"], "pending_approval")

    def test_send_failure_frees_target_no_permanent_lock(self):
        class BoomSender(FakeSender):
            def send_approval(self, **kw):
                raise RuntimeError("telegram down")
        out = self._req(BoomSender())
        self.assertEqual(out["error"], "send_failed")
        self.assertEqual(out["status"], "delivery_failed")
        self.assertEqual(cs.get_change_run(out["change_run_id"])["change_run_status"], "delivery_failed")
        # the target is NOT permanently locked — a subsequent plan succeeds
        self.assertTrue(self._req(FakeSender())["ok"])

    def test_real_sender_delivery_failure_frees_target(self):
        # integration via the REAL ChangeApprovalSender: a Telegram non-ok (429) must NOT
        # leave the run pending_approval — it becomes delivery_failed, freeing the target so
        # the NEXT plan succeeds.
        from app.change_executor import telegram
        env = {"ELIRA_CHANGE_BOT_TOKEN": "tok", "ITOPS_CHANGE_APPROVER_USER_IDS": "42",
               "ITOPS_CHANGE_APPROVER_CHAT_IDS": "100"}
        sender = telegram.ChangeApprovalSender()
        with unittest.mock.patch.dict(os.environ, env, clear=False), \
                unittest.mock.patch.object(telegram, "_tg", return_value={"ok": False, "error_code": 429}):
            out = ipc.request_plan("ai-server-netdata", sender=sender, registry_path=self.reg,
                                   runner=FakeRunner([("ok", F(1238))]))
        self.assertEqual(out["error"], "send_failed")
        self.assertEqual(out["status"], "delivery_failed")
        self.assertEqual(cs.get_change_run(out["change_run_id"])["change_run_status"], "delivery_failed")
        with unittest.mock.patch.dict(os.environ, env, clear=False), \
                unittest.mock.patch.object(telegram, "_tg", return_value={"ok": True}):
            out2 = ipc.request_plan("ai-server-netdata", sender=sender, registry_path=self.reg,
                                    runner=FakeRunner([("ok", F(1238))]))
        self.assertTrue(out2["ok"])                      # target was freed
        self.assertEqual(out2["status"], "pending_approval")

    def test_rate_limiter_threadsafe_under_barrier(self):
        import threading
        lim = ipc.RateLimiter(max_calls=3, window_seconds=1000.0)
        results: list[bool] = []
        rlock = threading.Lock()
        barrier = threading.Barrier(12)

        def worker():
            barrier.wait()                  # all threads race allow() simultaneously
            ok = lim.allow()
            with rlock:
                results.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for r in results if r), 3)   # exactly max — no over-admit

    def test_get_status_not_found(self):
        self.assertEqual(ipc.get_status("nope")["error"], "not_found")


if __name__ == "__main__":
    unittest.main()
