"""Workflow-owned network inventory without product authorization/budget caps."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.it_ops import net_inventory as ni  # noqa: E402
from app.application.tool_providers.itops_provider import (  # noqa: E402
    tool_itops_network_inventory,
)


class CidrContractTest(unittest.TestCase):
    def test_any_canonical_ipv4_network_is_accepted(self) -> None:
        private = ni.parse_cidr_v1("192.168.88.0/24")
        public = ni.parse_cidr_v1("8.8.0.0/16")
        self.assertEqual(str(private), "192.168.88.0/24")
        self.assertEqual(str(public), "8.8.0.0/16")
        self.assertEqual(ni.usable_host_count(private), 254)

    def test_noncanonical_and_ipv6_inputs_are_rejected_as_shape_errors(self) -> None:
        with self.assertRaises(ni.CidrError) as noncanonical:
            ni.parse_cidr_v1("192.168.88.5/24")
        self.assertEqual(
            noncanonical.exception.reason,
            "cidr_not_canonical_or_invalid",
        )

        with self.assertRaises(ni.CidrError) as ipv6:
            ni.parse_cidr_v1("fd00::/120")
        self.assertEqual(ipv6.exception.reason, "ipv6_not_supported")

    def test_compatibility_authorization_accepts_every_ipv4_destination(self) -> None:
        self.assertTrue(ni.cidr_authorized("192.168.88.0/24"))
        self.assertTrue(ni.cidr_authorized("8.8.8.0/24"))
        self.assertFalse(ni.cidr_authorized("fd00::/120"))
        self.assertFalse(ni.cidr_authorized("not-a-cidr"))
        self.assertEqual(ni.allowed_cidrs(), [])


class ProfileContractTest(unittest.TestCase):
    def test_profile_has_no_port_count_or_work_budget_cap(self) -> None:
        ports = tuple(range(1, 65))
        profile = ni.build_profile(
            ports,
            per_connect_timeout=2.5,
            in_flight=128,
        )
        ni.validate_profile(profile, host_count=1_000_000)
        self.assertEqual(profile.ports, ports)
        self.assertEqual(profile.per_connect_timeout, 2.5)
        self.assertEqual(profile.in_flight, 128)

    def test_profile_deduplicates_ports_and_validates_transport_values(self) -> None:
        profile = ni.build_profile([22, 22, 443])
        self.assertEqual(profile.ports, (22, 443))

        for ports in ([], [0], [65536], [True]):
            with self.subTest(ports=ports), self.assertRaises(ni.CidrError):
                ni.build_profile(ports)

        with self.assertRaises(ni.CidrError):
            ni.build_profile([22], per_connect_timeout=0)
        with self.assertRaises(ni.CidrError):
            ni.build_profile([22], in_flight=0)


class ScanSemanticsTest(unittest.TestCase):
    def test_scan_completes_every_supplied_target_without_wall_clock_stop(self) -> None:
        def connect(host: str, port: int, timeout: float) -> str:
            self.assertEqual(timeout, 0.5)
            if host == "192.168.99.1" and port == 80:
                return "open"
            if host == "192.168.99.2" and port == 443:
                return "refused"
            return "timeout"

        profile = ni.build_profile([80, 443], per_connect_timeout=0.5, in_flight=2)
        result = ni.run_scan(
            "192.168.99.0/30",
            ["192.168.99.1", "192.168.99.2"],
            profile,
            host_count=2,
            connect_fn=connect,
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.stop_reason, "complete")
        self.assertEqual(result.planned, 4)
        self.assertEqual(result.attempted, 4)
        self.assertEqual(result.completed, 4)
        self.assertEqual(result.opens, [{"host": "192.168.99.1", "port": 80}])
        self.assertEqual(result.counts["open"], 1)
        self.assertEqual(result.counts["refused"], 1)
        self.assertEqual(result.counts["timeout"], 2)

    def test_explicit_stop_is_the_only_product_level_partial_result(self) -> None:
        profile = ni.build_profile([22, 80], in_flight=2)
        result = ni.run_scan(
            "10.0.0.0/24",
            ["10.0.0.1", "10.0.0.2"],
            profile,
            host_count=2,
            connect_fn=lambda *_: "open",
            should_stop=lambda: True,
        )

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.stop_reason, "stopped")
        self.assertEqual(result.planned, 4)
        self.assertEqual(result.attempted, 0)
        self.assertEqual(result.completed, 0)

    def test_connect_errors_are_observed_not_promoted_to_product_guards(self) -> None:
        def connect(_host: str, port: int, _timeout: float) -> str:
            if port == 1:
                return "unreachable"
            raise OSError("boom")

        result = ni.run_scan(
            "10.0.0.1/32",
            ["10.0.0.1"],
            ni.build_profile([1, 2]),
            connect_fn=connect,
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.counts["unreachable"], 1)
        self.assertEqual(result.counts["local_error"], 1)


class NetworkInventoryToolContractTest(unittest.TestCase):
    def test_model_facing_text_names_each_confirmed_open_endpoint(self) -> None:
        scan_result = SimpleNamespace(
            opens=[{"host": "127.0.0.1", "port": 8000}],
            vantage="elira-local",
            source_ip="127.0.0.1",
            planned=2,
            attempted=2,
            completed=2,
            counts={
                "open": 1,
                "refused": 1,
                "timeout": 0,
                "unreachable": 0,
                "local_error": 0,
            },
            stop_reason="complete",
            status="complete",
        )
        with mock.patch.object(ni, "run_scan", return_value=scan_result), \
             mock.patch("app.infrastructure.it_ops.store.init_db"), \
             mock.patch("app.infrastructure.it_ops.store.record_evidence"):
            result = tool_itops_network_inventory(
                cidr="127.0.0.1/32",
                ports=[8000, 65534],
                connect_timeout=0.2,
                concurrency=2,
            )

        self.assertTrue(result["ok"])
        self.assertIn("open_endpoints=127.0.0.1:8000", result["text"])


if __name__ == "__main__":
    unittest.main()
