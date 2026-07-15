"""Executor-private change store (v2): explicit-verified migration incl. the EXACT partial
unique index DDL, atomic plan+capabilities creation, one-transaction capability consume +
CAS, token-guarded finalize, deadline-only sweep (CAS-winners only), fresh-inspect-gated
resolution, and the DB-enforced one-active-per-target invariant."""
from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.change_executor import store as cs  # noqa: E402


def _hashes(cid: str) -> tuple[str, str]:
    return (hashlib.sha256(f"approve-{cid}".encode()).hexdigest(),
            hashlib.sha256(f"reject-{cid}".encode()).hexdigest())


def _plan(target_id="ai-server-netdata", cid=None, argv_hash="argv1", snap_hash="snap1",
          pid=1238, ttl=300.0, now=1000.0):
    """Create a plan + both capabilities in one call; returns (cid, approve_hash, reject_hash)."""
    cid = cid or cs.new_change_run_id()
    ah, rh = _hashes(cid)
    cs.create_plan_with_capabilities(
        change_run_id=cid, target_id=target_id, unit="netdata.service", operation="restart",
        snapshot='{"active_state":"active"}', snapshot_main_pid=pid, snapshot_hash=snap_hash,
        planned_argv_hash=argv_hash, planned_binding='{}', approve_hash=ah, reject_hash=rh,
        capability_expires_at=now + ttl)
    return cid, ah, rh


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        cs._DB_PATH_OVERRIDE = str(Path(self._tmp) / "change.sqlite3")

    def tearDown(self):
        cs._DB_PATH_OVERRIDE = None

    def test_v0_creates_and_verifies_v2(self):
        cs.init_db()
        conn = cs._connect()
        try:
            self.assertEqual(int(conn.execute("PRAGMA user_version").fetchone()[0]), 2)
            cs._verify_contract(conn)
        finally:
            conn.close()
        cs.init_db()                    # idempotent verify-only

    def test_exact_v1_migrates_index_to_v2_and_locks_rollback_failed(self):
        conn = cs._connect()
        try:
            conn.executescript(cs._CREATE_SQL)
            conn.execute(f"DROP INDEX {cs._INDEX_NAME}")
            conn.execute(cs._V1_EXPECTED_INDEX_DDL)
            conn.execute("PRAGMA user_version=1")
            conn.commit()
        finally:
            conn.close()
        cs.init_db()
        conn = cs._connect()
        try:
            self.assertEqual(int(conn.execute("PRAGMA user_version").fetchone()[0]), 2)
            cs._verify_contract(conn)
            for cid in ("chg-a", "chg-b"):
                with self.subTest(cid=cid):
                    if cid == "chg-b":
                        with self.assertRaises(sqlite3.IntegrityError):
                            conn.execute(
                                "INSERT INTO change_runs (change_run_id,target_id,unit,operation,"
                                "change_run_status,snapshot,snapshot_hash,planned_argv_hash,"
                                "planned_binding,approver,attempt_token,created_at,updated_at) "
                                "VALUES (?,?,?,?,'rollback_failed','{}','','','{}','','',1,1)",
                                (cid, "same-target", "netdata.service", "set_update_every_1"))
                        break
                    conn.execute(
                        "INSERT INTO change_runs (change_run_id,target_id,unit,operation,"
                        "change_run_status,snapshot,snapshot_hash,planned_argv_hash,"
                        "planned_binding,approver,attempt_token,created_at,updated_at) "
                        "VALUES (?,?,?,?,'rollback_failed','{}','','','{}','','',1,1)",
                        (cid, "same-target", "netdata.service", "set_update_every_1"))
        finally:
            conn.close()

    def test_missing_partial_index_fails_closed(self):
        cs.init_db()
        conn = cs._connect()
        try:
            conn.execute(f"DROP INDEX {cs._INDEX_NAME}")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(cs.ChangeStoreUnavailable):
            cs.init_db()

    def test_weakened_index_predicate_fails_closed(self):
        # a predicate that still contains the state literals but is toothless (`AND 0`)
        # would pass a substring/predicate scan — the EXACT-DDL check must reject it.
        cs.init_db()
        conn = cs._connect()
        try:
            conn.execute(f"DROP INDEX {cs._INDEX_NAME}")
            conn.execute(
                f"CREATE UNIQUE INDEX {cs._INDEX_NAME} ON change_runs(target_id) "
                f"WHERE change_run_status IN ('pending_approval','applying','apply_unknown',"
                f"'rollback_failed') AND 0")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(cs.ChangeStoreUnavailable):
            cs.init_db()
        # and prove WHY it must be rejected: the weakened index enforces nothing, so two
        # active rows for the same target coexist.
        conn = cs._connect()
        try:
            for cid in ("chg-a", "chg-b"):
                conn.execute(
                    "INSERT INTO change_runs (change_run_id,target_id,unit,operation,"
                    "change_run_status,snapshot,snapshot_hash,planned_argv_hash,approver,"
                    "attempt_token,created_at,updated_at) VALUES (?,?,?,?,'pending_approval',"
                    "'{}','','','','',1.0,1.0)", (cid, "same-target", "u", "restart"))
            conn.commit()
            n = conn.execute("SELECT COUNT(*) FROM change_runs WHERE target_id='same-target' "
                             "AND change_run_status='pending_approval'").fetchone()[0]
            self.assertEqual(n, 2)      # toothless: two active plans passed
        finally:
            conn.close()

    def test_missing_path_fails_closed(self):
        import os
        cs._DB_PATH_OVERRIDE = None
        old = os.environ.pop("ELIRA_CHANGE_STORE_PATH", None)
        try:
            with self.assertRaises(cs.ChangeStoreUnavailable):
                cs.init_db()
        finally:
            if old is not None:
                os.environ["ELIRA_CHANGE_STORE_PATH"] = old


class StateMachineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        cs._DB_PATH_OVERRIDE = str(Path(self._tmp) / "change.sqlite3")
        cs.init_db()

    def tearDown(self):
        cs._DB_PATH_OVERRIDE = None

    def test_one_active_per_target_conflict(self):
        _plan()
        with self.assertRaises(cs.ActiveTargetConflict):
            _plan()                     # two active plans cannot pass

    def test_plan_and_both_capabilities_are_atomic(self):
        cid, ah, rh = _plan()
        conn = cs._connect()
        try:
            actions = {r[0] for r in conn.execute(
                "SELECT action FROM change_approvals WHERE change_run_id=?", (cid,)).fetchall()}
        finally:
            conn.close()
        self.assertEqual(actions, {"approve", "reject"})       # both minted with the plan

    def test_approve_consume_claims_applying_and_is_one_time(self):
        cid, ah, rh = _plan()
        out = cs.consume_capability(capability_hash=ah, approver="tg:42",
                                    apply_deadline_seconds=60.0, now=1000.0)
        self.assertIsNotNone(out)
        self.assertEqual(out["change_run_status"], "applying")
        self.assertEqual(out["approver"], "tg:42")
        self.assertTrue(out["attempt_token"])
        self.assertEqual(out["apply_deadline_at"], 1060.0)
        self.assertIsNone(cs.consume_capability(capability_hash=ah, approver="tg:42",
                                                apply_deadline_seconds=60.0, now=1001.0))  # replay no-op

    def test_consume_rejects_expired_and_mismatch(self):
        cid, ah, rh = _plan(ttl=10.0, now=1000.0)
        self.assertIsNone(cs.consume_capability(capability_hash=ah, approver="x",
                                                apply_deadline_seconds=60.0, now=2000.0))  # expired
        # mismatch defense: divergent argv_hash on the change_run → consume no-op, state unchanged
        cid2, ah2, rh2 = _plan(target_id="other-target")
        conn = cs._connect()
        try:
            conn.execute("UPDATE change_runs SET planned_argv_hash='DIVERGED' WHERE change_run_id=?", (cid2,))
            conn.commit()
        finally:
            conn.close()
        self.assertIsNone(cs.consume_capability(capability_hash=ah2, approver="x",
                                                apply_deadline_seconds=60.0, now=1000.0))
        self.assertEqual(cs.get_change_run(cid2)["change_run_status"], "pending_approval")

    def test_reject_consume(self):
        cid, ah, rh = _plan()
        out = cs.consume_capability(capability_hash=rh, approver="tg:42",
                                    apply_deadline_seconds=60.0, now=1000.0)
        self.assertEqual(out["change_run_status"], "rejected")

    def test_finalize_is_token_guarded(self):
        cid, ah, rh = _plan()
        claimed = cs.consume_capability(capability_hash=ah, approver="x",
                                        apply_deadline_seconds=60.0, now=1000.0)
        token = claimed["attempt_token"]
        self.assertFalse(cs.finalize_apply(change_run_id=cid, attempt_token="WRONG", status="applied"))
        self.assertTrue(cs.finalize_apply(change_run_id=cid, attempt_token=token, status="applied",
                                          verdict="active/running, MainPID changed"))
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "applied")

    def test_sweep_only_past_deadline_and_blocks_late_worker(self):
        cid, ah, rh = _plan()
        claimed = cs.consume_capability(capability_hash=ah, approver="x",
                                        apply_deadline_seconds=60.0, now=1000.0)   # deadline 1060
        token = claimed["attempt_token"]
        self.assertEqual(cs.sweep_stale_applying(now=1030.0), [])                  # before deadline
        self.assertEqual(cs.sweep_stale_applying(now=1100.0), [cid])               # CAS winner only
        self.assertEqual(cs.sweep_stale_applying(now=1200.0), [])                  # already swept → no re-win
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "apply_unknown")
        self.assertFalse(cs.finalize_apply(change_run_id=cid, attempt_token=token, status="applied"))
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "apply_unknown")

    GOOD_INSPECT = {"id": "netdata.service", "load_state": "loaded", "active_state": "active",
                    "sub_state": "running", "unit_file_state": "enabled", "main_pid": 1240,
                    "exec_main_status": 0, "n_restarts": 1,
                    "fragment_path": "/usr/lib/systemd/system/netdata.service"}

    def _to_apply_unknown(self, target_id="ai-server-netdata"):
        cid, ah, rh = _plan(target_id=target_id)
        cs.consume_capability(capability_hash=ah, approver="x", apply_deadline_seconds=60.0, now=1000.0)
        cs.sweep_stale_applying(now=1100.0)
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "apply_unknown")
        return cid

    def test_resolve_no_op_when_not_apply_unknown(self):
        cid, ah, rh = _plan()                                                      # still pending (locked)
        self.assertFalse(cs.resolve_after_inspect(change_run_id=cid, resolver="tg:42",
                         inspect_fields=self.GOOD_INSPECT, inspect_exit_status="0"))
        self.assertEqual(cs.list_change_evidence(cid), [])                         # no evidence
        with self.assertRaises(cs.ActiveTargetConflict):
            _plan()                                                                # still locked

    def test_resolve_rejects_invalid_or_nonzero_inspect_and_stays_locked(self):
        # a REAL apply_unknown, but the resolution inspect is empty / missing a required
        # field / non-zero exit → False, NO evidence, target stays locked.
        cases = (
            ({}, "0"),                                                             # empty
            ({"id": "netdata.service", "load_state": "loaded",                     # missing active_state
              "sub_state": "running", "main_pid": 1240}, "0"),
            ({**self.GOOD_INSPECT, "main_pid": "notint"}, "0"),                    # main_pid not int
            (self.GOOD_INSPECT, "1"),                                              # non-zero exit
        )
        for i, (fields, exit_status) in enumerate(cases):
            cid = self._to_apply_unknown(target_id=f"target-{i}")                  # unique locked target
            self.assertFalse(cs.resolve_after_inspect(change_run_id=cid, resolver="tg:42",
                             inspect_fields=fields, inspect_exit_status=exit_status), (fields, exit_status))
            self.assertEqual(cs.list_change_evidence(cid), [])                     # no evidence written
            self.assertEqual(cs.get_change_run(cid)["change_run_status"], "apply_unknown")  # still locked

    def test_resolve_with_valid_fresh_inspect_frees_target(self):
        cid = self._to_apply_unknown()
        self.assertTrue(cs.resolve_after_inspect(change_run_id=cid, resolver="tg:42",
                        inspect_fields=self.GOOD_INSPECT, inspect_exit_status="0"))
        ev = cs.list_change_evidence(cid)
        self.assertEqual([e["operation"] for e in ev], ["systemd_change:resolution_inspect"])  # store-fixed op
        self.assertEqual(ev[0]["exit_status"], "0")
        import json as _json
        stored = _json.loads(ev[0]["result"])
        self.assertEqual(stored["active_state"], "active")
        self.assertEqual(stored["main_pid"], 1240)
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "resolved_unknown")
        _plan(target_id="ai-server-netdata")                                       # target freed

    def test_resolve_rejects_inspect_of_wrong_unit(self):
        # apply_unknown is for netdata.service; a well-formed inspect of a DIFFERENT unit
        # (sshd.service) must not resolve it.
        cid = self._to_apply_unknown()
        wrong_unit = {**self.GOOD_INSPECT, "id": "sshd.service"}                   # valid shape, wrong unit
        self.assertFalse(cs.resolve_after_inspect(change_run_id=cid, resolver="tg:42",
                         inspect_fields=wrong_unit, inspect_exit_status="0"))
        self.assertEqual(cs.list_change_evidence(cid), [])                         # no evidence
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "apply_unknown")  # still locked
        with self.assertRaises(cs.ActiveTargetConflict):
            _plan(target_id="ai-server-netdata")                                   # target still locked

    def test_terminal_frees_target_for_new_plan(self):
        cid, ah, rh = _plan()
        cs.consume_capability(capability_hash=rh, approver="x", apply_deadline_seconds=60.0, now=1000.0)
        _plan()                                                                    # rejected freed the target

    def test_mark_delivery_failed_frees_target_and_invalidates_caps(self):
        cid, ah, rh = _plan()
        self.assertTrue(cs.mark_delivery_failed(change_run_id=cid))
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "delivery_failed")   # terminal
        # capabilities invalidated → a consume is a no-op
        self.assertIsNone(cs.consume_capability(capability_hash=ah, approver="x",
                                                apply_deadline_seconds=60.0, now=1000.0))
        _plan()                                    # target freed (delivery_failed not active)

    def test_expire_pending_returns_cas_winners_only(self):
        cid, ah, rh = _plan(ttl=10.0, now=1000.0)
        self.assertEqual(cs.expire_pending(now=2000.0), [cid])
        self.assertEqual(cs.expire_pending(now=3000.0), [])                        # already expired → no re-win
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "expired")


if __name__ == "__main__":
    unittest.main()
