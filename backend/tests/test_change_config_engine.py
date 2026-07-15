"""Typed Netdata config target through the existing approval/change engine."""
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

from app.change_executor import engine, registry, remote_netdata_config, store as cs, transport  # noqa: E402

HELPER_SHA = "a" * 64
BEFORE_SHA = "b" * 64
AFTER_SHA = "c" * 64


def _inspect(*, before=BEFORE_SHA, planned=AFTER_SHA, pid=100) -> dict:
    return {
        "protocol": 1, "helper_sha256": HELPER_SHA, "status": "ok",
        "config_id": "netdata-main", "path": "/etc/netdata/netdata.conf",
        "before_sha256": before, "planned_sha256": planned, "size_bytes": 326,
        "comment_only": True, "safe_settings": {},
        "desired_settings": {"global.update_every": 1},
        "service": {"id": "netdata.service", "load_state": "loaded",
                    "active_state": "active", "sub_state": "running", "main_pid": pid},
    }


class ConfigRunner:
    def __init__(self, inspects, apply_payload=None, apply_exit=0, apply_timeout=False):
        self.inspects = list(inspects)
        self.apply_payload = apply_payload or {
            "status": "applied", "helper_sha256": HELPER_SHA,
            "before_sha256": BEFORE_SHA, "after_sha256": AFTER_SHA,
            "rollback_attempted": False,
            "service": {"id": "netdata.service", "load_state": "loaded",
                        "active_state": "active", "sub_state": "running", "main_pid": 101},
        }
        self.apply_exit = apply_exit
        self.apply_timeout = apply_timeout
        self.inspect_calls = 0
        self.apply_calls = 0
        self.argvs = []

    def __call__(self, argv, timeout):
        self.argvs.append(list(argv))
        if "apply" in argv:
            self.apply_calls += 1
            if self.apply_timeout:
                raise subprocess.TimeoutExpired(argv, timeout)
            return transport.SshResult(self.apply_exit,
                                       json.dumps(self.apply_payload).encode(), b"")
        self.inspect_calls += 1
        payload = self.inspects[min(self.inspect_calls - 1, len(self.inspects) - 1)]
        return transport.SshResult(0, json.dumps(payload).encode(), b"")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class ConfigEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cs._DB_PATH_OVERRIDE = str(self.tmp / "change.sqlite3")
        cs.init_db()
        self.reg = self.tmp / "registry.json"
        self.reg.write_text(json.dumps({"targets": {
            "ai-server-netdata-config": {
                "target_kind": "netdata_config", "host": "192.168.88.15", "port": 22,
                "remote_user": "elira-change", "known_hosts": "/kh", "identity_file": "/id",
                "unit": "netdata.service", "operation": "set_update_every_1",
                "config_id": "netdata-main",
                "helper_path": "/usr/local/sbin/elira-netdata-config",
                "helper_sha256": HELPER_SHA,
            }
        }}), encoding="utf-8")

    def tearDown(self):
        cs._DB_PATH_OVERRIDE = None

    def _plan(self, runner):
        return engine.plan("ai-server-netdata-config", registry_path=str(self.reg),
                           runner=runner, clock=lambda: 1000.0)

    def _approve(self, plan):
        row = cs.consume_capability(
            capability_hash=_sha(plan["approve_token"]), approver="tg:42",
            apply_deadline_seconds=engine.APPLY_DEADLINE_SECONDS, now=1000.0)
        self.assertEqual(row["change_run_status"], "applying")
        return plan["change_run_id"]

    def _apply(self, cid, runner):
        return engine.apply(cid, registry_path=str(self.reg), runner=runner,
                            sleep=lambda _s: None)

    def test_plan_binds_only_fixed_helper_run_hashes_and_no_content(self):
        plan = self._plan(ConfigRunner([_inspect()]))
        argv = plan["planned_argv"]
        self.assertEqual(argv[-8], "/usr/local/sbin/elira-netdata-config")
        self.assertEqual(argv[-7], "apply")
        self.assertIn(plan["change_run_id"], argv)
        self.assertIn(BEFORE_SHA, argv)
        self.assertIn(AFTER_SHA, argv)
        blob = " ".join(argv)
        for forbidden in ("/etc/netdata/netdata.conf", "update every", "[global]", "= 1"):
            self.assertNotIn(forbidden, blob)

    def test_outer_timeouts_cover_the_bounded_remote_failure_and_rollback_path(self):
        postcheck = (
            remote_netdata_config.POSTCHECK_ATTEMPTS
            * remote_netdata_config.SERVICE_INSPECT_TIMEOUT
            + (remote_netdata_config.POSTCHECK_ATTEMPTS - 1)
            * remote_netdata_config.POSTCHECK_INTERVAL
        )
        remote_worst = (
            remote_netdata_config.SERVICE_INSPECT_TIMEOUT
            + remote_netdata_config.RESTART_TIMEOUT
            + postcheck
            + remote_netdata_config.SERVICE_INSPECT_TIMEOUT
            + remote_netdata_config.RESTART_TIMEOUT
            + postcheck
        )
        self.assertGreater(transport.CONFIG_APPLY_TIMEOUT, remote_worst)
        self.assertGreater(engine.APPLY_DEADLINE_SECONDS, transport.CONFIG_APPLY_TIMEOUT)

    def test_plan_rejects_helper_hash_mismatch_and_noop(self):
        bad = _inspect()
        bad["helper_sha256"] = "d" * 64
        with self.assertRaises(engine.PreconditionError):
            self._plan(ConfigRunner([bad]))
        with self.assertRaises(engine.PreconditionError):
            self._plan(ConfigRunner([_inspect(planned=BEFORE_SHA)]))

    def test_registry_rejects_arbitrary_helper_path_or_unpinned_helper(self):
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        for key, value in (("helper_path", "/tmp/attacker-helper"),
                           ("helper_sha256", "not-a-hash")):
            bad = json.loads(json.dumps(data))
            bad["targets"]["ai-server-netdata-config"][key] = value
            self.reg.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(registry.RegistryError):
                registry.resolve("ai-server-netdata-config", path=str(self.reg))
        self.reg.write_text(json.dumps(data), encoding="utf-8")

    def test_apply_success(self):
        cid = self._approve(self._plan(ConfigRunner([_inspect()])))
        runner = ConfigRunner([_inspect()])
        self.assertEqual(self._apply(cid, runner), "applied")
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "applied")
        self.assertEqual(runner.apply_calls, 1)

    def test_apply_success_requires_changed_main_pid(self):
        cid = self._approve(self._plan(ConfigRunner([_inspect()])))
        payload = ConfigRunner([_inspect()]).apply_payload
        payload["service"]["main_pid"] = 100
        self.assertEqual(self._apply(cid, ConfigRunner([_inspect()], payload)),
                         "apply_unknown")
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "apply_unknown")

    def test_preapply_drift_aborts_before_helper_apply(self):
        cid = self._approve(self._plan(ConfigRunner([_inspect()])))
        runner = ConfigRunner([_inspect(before="d" * 64)])
        self.assertEqual(self._apply(cid, runner), "aborted_before_apply")
        self.assertEqual(runner.apply_calls, 0)

    def test_definite_failure_with_successful_rollback_is_terminal(self):
        cid = self._approve(self._plan(ConfigRunner([_inspect()])))
        payload = {
            "status": "rolled_back", "reason": "postcheck_failed",
            "helper_sha256": HELPER_SHA, "before_sha256": BEFORE_SHA,
            "after_sha256": AFTER_SHA, "rollback_attempted": True,
            "service": {"id": "netdata.service", "load_state": "loaded",
                        "active_state": "active", "sub_state": "running", "main_pid": 102},
        }
        self.assertEqual(self._apply(cid, ConfigRunner([_inspect()], payload, apply_exit=20)),
                         "rolled_back")
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "rolled_back")

    def test_rollback_failed_stays_db_locked_until_typed_resolution(self):
        cid = self._approve(self._plan(ConfigRunner([_inspect()])))
        payload = {
            "status": "rollback_failed", "reason": "rollback_cas_mismatch",
            "helper_sha256": HELPER_SHA, "before_sha256": BEFORE_SHA,
            "after_sha256": AFTER_SHA, "rollback_attempted": True,
        }
        self.assertEqual(self._apply(cid, ConfigRunner([_inspect()], payload, apply_exit=21)),
                         "rollback_failed")
        with self.assertRaises(cs.ActiveTargetConflict):
            self._plan(ConfigRunner([_inspect()]))
        # Fresh inspect proves the original hash + healthy unit: resolve as rolled back.
        self.assertTrue(engine.resolve(
            cid, resolver="tg:42", registry_path=str(self.reg),
            runner=ConfigRunner([_inspect(before=BEFORE_SHA, planned=AFTER_SHA, pid=103)])))
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "resolved_rolled_back")

    def test_transport_timeout_is_unknown_then_fresh_desired_state_resolves_applied(self):
        cid = self._approve(self._plan(ConfigRunner([_inspect()])))
        self.assertEqual(self._apply(cid, ConfigRunner([_inspect()], apply_timeout=True)),
                         "apply_unknown")
        desired_now = _inspect(before=AFTER_SHA, planned=AFTER_SHA, pid=104)
        self.assertTrue(engine.resolve(
            cid, resolver="tg:42", registry_path=str(self.reg),
            runner=ConfigRunner([desired_now])))
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "resolved_applied")

    def test_helper_status_exit_mismatch_is_unknown_not_success(self):
        cid = self._approve(self._plan(ConfigRunner([_inspect()])))
        runner = ConfigRunner([_inspect()], apply_exit=22)
        self.assertEqual(self._apply(cid, runner), "apply_unknown")
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "apply_unknown")
        evidence = cs.list_change_evidence(cid)
        self.assertEqual(json.loads(evidence[-1]["result"])["outcome"],
                         "helper_exit_status_mismatch")

    def test_unknown_does_not_resolve_applied_without_service_restart_evidence(self):
        cid = self._approve(self._plan(ConfigRunner([_inspect()])))
        self.assertEqual(self._apply(cid, ConfigRunner([_inspect()], apply_timeout=True)),
                         "apply_unknown")
        desired_but_old_process = _inspect(before=AFTER_SHA, planned=AFTER_SHA, pid=100)
        self.assertFalse(engine.resolve(
            cid, resolver="tg:42", registry_path=str(self.reg),
            runner=ConfigRunner([desired_but_old_process])))
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "apply_unknown")


if __name__ == "__main__":
    unittest.main()
