"""Phase 3 — Network Inventory scanner: CIDR parsing/authorization, profile-cap
compatibility, bounded scan semantics (states, complete vs timed_out/partial)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.it_ops import net_inventory as ni  # noqa: E402


def _profile(ports=(22, 80, 443), rate=0, in_flight=8, per_ct=0.1, total=60.0):
    return ni.PortProfile(name="test", ports=tuple(ports), rate_limit=rate,
                          total_timeout=total, per_connect_timeout=per_ct,
                          in_flight=in_flight, max_hosts=254, min_prefix=24)


class CidrParseTest(unittest.TestCase):
    def test_valid_private_24(self):
        hosts = ni.parse_cidr_v1("192.168.88.0/24")
        self.assertEqual(len(hosts), 254)
        self.assertEqual(hosts[0], "192.168.88.1")

    def test_ipv6_rejected_400(self):
        with self.assertRaises(ni.CidrError) as c:
            ni.parse_cidr_v1("fd00::/120")
        self.assertEqual(c.exception.http_status, 400)
        self.assertEqual(c.exception.reason, "ipv6_not_supported")

    def test_non_canonical_rejected_400(self):
        with self.assertRaises(ni.CidrError) as c:
            ni.parse_cidr_v1("192.168.88.5/24")   # host bits set → strict fails
        self.assertEqual(c.exception.http_status, 400)

    def test_prefix_too_large_rejected_400(self):
        with self.assertRaises(ni.CidrError) as c:
            ni.parse_cidr_v1("10.0.0.0/16")
        self.assertEqual(c.exception.reason, "cidr_too_large")

    def test_public_rejected_403(self):
        with self.assertRaises(ni.CidrError) as c:
            ni.parse_cidr_v1("8.8.8.0/24")
        self.assertEqual(c.exception.http_status, 403)
        self.assertEqual(c.exception.reason, "public_cidr")


class CidrAuthorizationTest(unittest.TestCase):
    def test_only_allowlist_subset_is_authorized(self):
        import unittest.mock as m
        with m.patch.dict("os.environ", {"ITOPS_NETWORK_ALLOWED_CIDRS": "192.168.88.0/24, 10.10.0.0/16"}):
            self.assertTrue(ni.cidr_authorized("192.168.88.0/24"))     # subset (equal)
            self.assertTrue(ni.cidr_authorized("10.10.5.0/24"))        # strict subset
            self.assertFalse(ni.cidr_authorized("192.168.1.0/24"))     # not covered
            self.assertFalse(ni.cidr_authorized("10.0.0.0/8"))         # superset, not subset

    def test_default_deny_when_unset(self):
        import unittest.mock as m
        with m.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("ITOPS_NETWORK_ALLOWED_CIDRS", None)
            self.assertFalse(ni.cidr_authorized("192.168.88.0/24"))    # nothing authorized


class ProfileCompatTest(unittest.TestCase):
    def test_common_v1_fits_a_24(self):
        ni.validate_profile(ni.PROFILE_COMMON_V1, 254)                 # 2032 <= 3000*0.9

    def test_profile_exceeding_budget_rejected(self):
        bad = _profile(ports=(1, 2, 3, 4, 5, 6, 7, 8), rate=5, total=10.0)  # budget 50
        with self.assertRaises(ni.CidrError) as c:
            ni.validate_profile(bad, 254)                             # 2032 >> 45
        self.assertEqual(c.exception.reason, "profile_exceeds_budget")

    def test_too_many_ports_rejected(self):
        with self.assertRaises(ni.CidrError):
            ni.validate_profile(_profile(ports=tuple(range(9)), rate=1000, total=60.0), 1)


class ScanSemanticsTest(unittest.TestCase):
    def test_complete_counts_states_and_opens(self):
        # a mixed responder: only :80 open on .1; .2:443 refused; everything else timeout.
        def connect(host, port, timeout):
            if host == "192.168.99.1" and port == 80:
                return "open"
            if host == "192.168.99.2" and port == 443:
                return "refused"
            return "timeout"
        hosts = ["192.168.99.1", "192.168.99.2"]
        res = ni.run_scan("192.168.99.0/30", hosts, _profile(ports=(80, 443)), connect_fn=connect)
        self.assertEqual(res.status, "complete")
        self.assertEqual(res.stop_reason, "complete")
        self.assertEqual(res.planned, 4)                              # 2 hosts x 2 ports
        self.assertEqual(res.attempted, 4)
        self.assertEqual(res.completed, 4)
        self.assertEqual(res.opens, [{"host": "192.168.99.1", "port": 80}])   # only OPEN
        self.assertEqual(res.counts["open"], 1)
        self.assertEqual(res.counts["refused"], 1)
        self.assertEqual(res.counts["timeout"], 2)
        # a non-open result is never counted as "closed"
        self.assertNotIn("closed", res.counts)

    def test_timed_out_is_partial_not_empty(self):
        calls = [0]

        def mono():
            v = 0.0 if calls[0] == 0 else 1000.0   # deadline blows immediately after start
            calls[0] += 1
            return v
        res = ni.run_scan("192.168.99.0/24", ni.parse_cidr_v1("192.168.99.0/24"),
                          ni.PROFILE_COMMON_V1, connect_fn=lambda *a: "open", monotonic=mono)
        self.assertEqual(res.status, "timed_out")
        self.assertEqual(res.stop_reason, "timed_out")
        self.assertLess(res.attempted, res.planned)                   # NOT everything attempted
        self.assertGreater(res.planned, 0)                            # so it is not "empty/nothing found"

    def test_all_states_distinguished(self):
        def connect(host, port, timeout):
            return {1: "open", 2: "refused", 3: "timeout", 4: "unreachable"}.get(port, "local_error")
        res = ni.run_scan("192.168.99.0/32", ["192.168.99.1"],
                          _profile(ports=(1, 2, 3, 4, 5)), connect_fn=connect)
        self.assertEqual(res.counts["open"], 1)
        self.assertEqual(res.counts["refused"], 1)
        self.assertEqual(res.counts["timeout"], 1)
        self.assertEqual(res.counts["unreachable"], 1)
        self.assertEqual(res.counts["local_error"], 1)
        self.assertEqual(len(res.opens), 1)


class DeadlineHardnessTest(unittest.TestCase):
    """total_timeout is a HARD runtime cap, not just a timestamp: the scanner does not
    submit a full in-flight window past the deadline, and no connect is ever given a
    timeout that could outlive the overall deadline."""

    @staticmethod
    def _stepping_clock(dt):
        t = [0.0]

        def mono():
            v = t[0]
            t[0] += dt
            return v
        return mono

    def test_deadline_bounds_submissions_and_connect_timeouts(self):
        # per_connect_timeout (5.0) deliberately DWARFS total_timeout (0.5): the buggy
        # code submitted a full 64-wide window carrying the 5.0 per-connect timeout even
        # with a ~0 budget. The fix must (a) stop submitting at the deadline and (b) cap
        # each connect at the remaining budget.
        import threading
        seen: list[float] = []
        lock = threading.Lock()

        def connect(host, port, timeout):
            with lock:
                seen.append(timeout)
            return "timeout"

        profile = _profile(ports=tuple(range(1, 9)), rate=0, in_flight=64,
                           per_ct=5.0, total=0.5)
        hosts = ni.parse_cidr_v1("192.168.50.0/24")          # 254 x 8 = 2032 planned
        res = ni.run_scan("192.168.50.0/24", hosts, profile,
                          connect_fn=connect, monotonic=self._stepping_clock(0.05))
        self.assertEqual(res.status, "timed_out")
        self.assertEqual(res.stop_reason, "timed_out")
        self.assertGreater(res.attempted, 0)
        self.assertLess(res.attempted, res.planned)          # NOT the whole plan / a full window
        self.assertEqual(len(seen), res.attempted)           # every attempt is a real submit
        # the discriminating invariant: no connect outlives the overall deadline —
        # every timeout is min(per_connect_timeout, remaining) <= total_timeout.
        self.assertLessEqual(max(seen), profile.total_timeout)
        self.assertGreater(min(seen), 0.0)                   # never submit with a dead budget

    def test_full_budget_uses_full_per_connect_timeout(self):
        # when there IS budget, the min() cap must not wrongly truncate the connect.
        seen: list[float] = []

        def connect(host, port, timeout):
            seen.append(timeout)
            return "refused"

        profile = _profile(ports=(80,), rate=0, in_flight=4, per_ct=0.7, total=60.0)
        res = ni.run_scan("192.168.50.0/30", ni.parse_cidr_v1("192.168.50.0/30"),
                          profile, connect_fn=connect)       # real clock; instant connect
        self.assertEqual(res.status, "complete")
        self.assertTrue(seen)
        self.assertTrue(all(t == profile.per_connect_timeout for t in seen))


if __name__ == "__main__":
    unittest.main()
