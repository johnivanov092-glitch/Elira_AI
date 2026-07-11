"""Change engine state machine over a FAKE transport — every apply/drift/post-check/
resolve terminal path is exercised without a host. The real host is only ever driven by
the success + reject smoke."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.change_executor import engine, store as cs, transport  # noqa: E402

_PROP = {"id": "Id", "load_state": "LoadState", "active_state": "ActiveState",
         "sub_state": "SubState", "unit_file_state": "UnitFileState", "main_pid": "MainPID",
         "exec_main_status": "ExecMainStatus", "n_restarts": "NRestarts", "fragment_path": "FragmentPath"}


def _show_bytes(fields: dict) -> bytes:
    return ("\n".join(f"{_PROP[k]}={v}" for k, v in fields.items() if k in _PROP) + "\n").encode()


def F(pid, active=True, unit="netdata.service"):
    return {"id": unit, "load_state": "loaded",
            "active_state": "active" if active else "inactive",
            "sub_state": "running" if active else "dead", "unit_file_state": "enabled",
            "main_pid": pid, "exec_main_status": 0, "n_restarts": 0,
            "fragment_path": "/usr/lib/systemd/system/netdata.service"}


class FakeRunner:
    """Scripts inspect results (by call order, last repeats) and one apply result.
    spec: ('ok', fields) | ('nonzero', code) | ('timeout',) | ('error',) for inspects;
          ('exit', code) | ('timeout',) | ('error',) for apply."""

    def __init__(self, inspects, apply_result=("exit", 0)):
        self.inspects = list(inspects)
        self.apply_result = apply_result
        self.inspect_calls = 0
        self.apply_calls = 0

    def __call__(self, argv, timeout):
        if "restart" in argv:
            self.apply_calls += 1
            r = self.apply_result
            if r[0] == "timeout":
                raise subprocess.TimeoutExpired(argv, timeout)
            if r[0] == "error":
                raise OSError("ssh not found")
            return transport.SshResult(r[1], b"", b"" if r[1] == 0 else b"restart failed")
        self.inspect_calls += 1
        spec = self.inspects[min(self.inspect_calls - 1, len(self.inspects) - 1)]
        if spec[0] == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout)
        if spec[0] == "error":
            raise OSError("ssh not found")
        if spec[0] == "nonzero":
            return transport.SshResult(spec[1], b"", b"show failed")
        return transport.SshResult(0, _show_bytes(spec[1]), b"")


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


class EngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        cs._DB_PATH_OVERRIDE = str(Path(self._tmp) / "change.sqlite3")
        cs.init_db()
        self.reg = str(Path(self._tmp) / "registry.json")
        Path(self.reg).write_text(json.dumps({"targets": {"ai-server-netdata": {
            "host": "192.168.88.15", "port": 22, "remote_user": "elira-change",
            "known_hosts": "/etc/elira-change/known_hosts", "identity_file": "/etc/elira-change/id",
            "unit": "netdata.service", "operation": "restart"}}}), encoding="utf-8")

    def tearDown(self):
        cs._DB_PATH_OVERRIDE = None

    def _plan(self, plan_runner):
        return engine.plan("ai-server-netdata", registry_path=self.reg, runner=plan_runner,
                           clock=lambda: 1000.0)

    def _approve(self, plan):
        out = cs.consume_capability(capability_hash=_sha(plan["approve_token"]), approver="tg:42",
                                    apply_deadline_seconds=engine.APPLY_DEADLINE_SECONDS, now=1000.0)
        self.assertEqual(out["change_run_status"], "applying")
        return plan["change_run_id"]

    def _apply(self, cid, apply_runner):
        return engine.apply(cid, registry_path=self.reg, runner=apply_runner, sleep=lambda _s: None)

    def _ops(self, cid):
        return [e["operation"] for e in cs.list_change_evidence(cid)]

    # ── plan ────────────────────────────────────────────────────────────────
    def test_plan_refused_when_not_running(self):
        with self.assertRaises(engine.PreconditionError):
            self._plan(FakeRunner([("ok", F(1238, active=False))]))

    def test_plan_argv_is_fixed_sudo_restart(self):
        plan = self._plan(FakeRunner([("ok", F(1238))]))
        self.assertEqual(plan["planned_argv"][-5:],
                         ["sudo", "-n", "/usr/bin/systemctl", "restart", "netdata.service"])
        self.assertIn("StrictHostKeyChecking=yes", plan["planned_argv"])

    def test_hermetic_exact_argv(self):
        from app.change_executor import registry, transport
        t = registry.resolve("ai-server-netdata", path=self.reg)
        base = ["ssh", "-F", "none", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", "GlobalKnownHostsFile=none",
                "-o", "UserKnownHostsFile=/etc/elira-change/known_hosts",
                "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none", "-o", "UpdateHostKeys=no",
                "-o", "ConnectTimeout=10", "-i", "/etc/elira-change/id", "-p", "22",
                "elira-change@192.168.88.15"]
        self.assertEqual(transport.inspect_argv(t), base + [
            "systemctl", "show", "-p",
            "Id,LoadState,ActiveState,SubState,UnitFileState,MainPID,ExecMainStatus,NRestarts,FragmentPath",
            "netdata.service"])
        self.assertEqual(transport.apply_argv(t),
                         base + ["sudo", "-n", "/usr/bin/systemctl", "restart", "netdata.service"])

    def test_aborted_on_binding_drift_no_ssh(self):
        cid = self._approve(self._plan(FakeRunner([("ok", F(1238))])))
        # registry edited so the target host changed since plan → binding drift
        Path(self.reg).write_text(json.dumps({"targets": {"ai-server-netdata": {
            "host": "10.0.0.9", "port": 22, "remote_user": "elira-change",
            "known_hosts": "/etc/elira-change/known_hosts", "identity_file": "/etc/elira-change/id",
            "unit": "netdata.service", "operation": "restart"}}}), encoding="utf-8")
        r = FakeRunner([("ok", F(1238))], ("exit", 0))
        self.assertEqual(self._apply(cid, r), "aborted_before_apply")
        self.assertEqual(r.inspect_calls, 0)      # binding checked BEFORE any SSH
        self.assertEqual(r.apply_calls, 0)

    def test_aborted_on_known_hosts_content_change(self):
        kh = str(Path(self._tmp) / "known_hosts")
        Path(kh).write_text("srv ssh-ed25519 AAAApinned\n", encoding="utf-8")
        Path(self.reg).write_text(json.dumps({"targets": {"ai-server-netdata": {
            "host": "192.168.88.15", "port": 22, "remote_user": "elira-change",
            "known_hosts": kh, "identity_file": "/id", "unit": "netdata.service",
            "operation": "restart"}}}), encoding="utf-8")
        cid = self._approve(self._plan(FakeRunner([("ok", F(1238))])))
        Path(kh).write_text("srv ssh-ed25519 SWAPPEDkey\n", encoding="utf-8")   # pin swapped
        r = FakeRunner([("ok", F(1238))], ("exit", 0))
        self.assertEqual(self._apply(cid, r), "aborted_before_apply")
        self.assertEqual(r.inspect_calls, 0)

    # ── apply terminal paths ─────────────────────────────────────────────────
    def test_applied(self):
        cid = self._approve(self._plan(FakeRunner([("ok", F(1238))])))
        # drift: same pid 1238 active; apply exit0; postcheck: pid 1240 active → applied
        status = self._apply(cid, FakeRunner([("ok", F(1238)), ("ok", F(1240))], ("exit", 0)))
        self.assertEqual(status, "applied")
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "applied")
        self.assertIn("systemd_change:postcheck", self._ops(cid))

    def test_aborted_before_apply_on_drift(self):
        cid = self._approve(self._plan(FakeRunner([("ok", F(1238))])))
        r = FakeRunner([("ok", F(9999))], ("exit", 0))            # MainPID changed since plan
        status = self._apply(cid, r)
        self.assertEqual(status, "aborted_before_apply")
        self.assertEqual(r.apply_calls, 0)                        # NO restart ran
        self.assertIn("systemd_change:aborted_before_apply", self._ops(cid))

    def test_command_failed_on_nonzero(self):
        cid = self._approve(self._plan(FakeRunner([("ok", F(1238))])))
        status = self._apply(cid, FakeRunner([("ok", F(1238))], ("exit", 1)))
        self.assertEqual(status, "command_failed")

    def test_apply_unknown_on_timeout(self):
        cid = self._approve(self._plan(FakeRunner([("ok", F(1238))])))
        status = self._apply(cid, FakeRunner([("ok", F(1238))], ("timeout",)))
        self.assertEqual(status, "apply_unknown")

    def test_apply_unknown_on_ssh_255(self):
        cid = self._approve(self._plan(FakeRunner([("ok", F(1238))])))
        status = self._apply(cid, FakeRunner([("ok", F(1238))], ("exit", 255)))
        self.assertEqual(status, "apply_unknown")

    def test_postcheck_failed_when_definite_but_pid_unchanged(self):
        cid = self._approve(self._plan(FakeRunner([("ok", F(1238))])))
        # drift ok; apply exit0; every post-inspect definite but MainPID still 1238 → failed
        status = self._apply(cid, FakeRunner([("ok", F(1238))], ("exit", 0)))
        self.assertEqual(status, "postcheck_failed")

    def test_apply_unknown_when_postcheck_indefinite(self):
        cid = self._approve(self._plan(FakeRunner([("ok", F(1238))])))
        # drift ok (index0); apply exit0; post-inspect nonzero (indefinite) → apply_unknown
        status = self._apply(cid, FakeRunner([("ok", F(1238)), ("nonzero", 3)], ("exit", 0)))
        self.assertEqual(status, "apply_unknown")

    def test_apply_noop_when_not_applying(self):
        # a run not in `applying` (e.g. never approved) is untouched
        plan = self._plan(FakeRunner([("ok", F(1238))]))
        self.assertEqual(self._apply(plan["change_run_id"], FakeRunner([("ok", F(1238))])),
                         "pending_approval")

    # ── resolve ──────────────────────────────────────────────────────────────
    def _to_unknown(self):
        cid = self._approve(self._plan(FakeRunner([("ok", F(1238))])))
        self._apply(cid, FakeRunner([("ok", F(1238))], ("timeout",)))    # → apply_unknown
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "apply_unknown")
        return cid

    def test_resolve_with_fresh_inspect_frees_target(self):
        cid = self._to_unknown()
        self.assertTrue(engine.resolve(cid, resolver="tg:42", registry_path=self.reg,
                        runner=FakeRunner([("ok", F(1240))])))
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "resolved_unknown")
        self.assertIn("systemd_change:resolution_inspect", self._ops(cid))

    def test_resolve_rejects_wrong_unit_inspect(self):
        cid = self._to_unknown()
        self.assertFalse(engine.resolve(cid, resolver="tg:42", registry_path=self.reg,
                         runner=FakeRunner([("ok", F(1240, unit="sshd.service"))])))
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "apply_unknown")

    def test_resolve_rejects_indefinite_inspect(self):
        cid = self._to_unknown()
        self.assertFalse(engine.resolve(cid, resolver="tg:42", registry_path=self.reg,
                         runner=FakeRunner([("nonzero", 4)])))
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "apply_unknown")

    def test_final_reports_db_status_when_cas_lost(self):
        # the sweep-wins-during-postcheck race: the run is already apply_unknown, so a late
        # _final('applied') CAS loses → must report the ACTUAL apply_unknown, not 'applied'.
        cid = self._to_unknown()
        token = cs.get_change_run(cid)["attempt_token"]
        self.assertEqual(engine._final(cid, token, "applied", "active/running"), "apply_unknown")
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "apply_unknown")


if __name__ == "__main__":
    unittest.main()
