"""Phase 7A — MikroTik read-only inventory projection: valid projection,
fail-closed malformed payloads, routerId consistency, caps + truncation
reporting, stable ordering, secret/unknown-field scrubbing, and purity
(no subprocess/socket/HTTP/MCP calls)."""
from __future__ import annotations

import ast
import json
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.it_ops import mikrotik_inventory as mi  # noqa: E402


def _env(router_id="lab-router", **structured):
    """A raw MCP tools/call result envelope as the existing transport returns it."""
    return {
        "content": [{"type": "text", "text": "human summary, ignored by projector"}],
        "structuredContent": {"routerId": router_id, **structured},
    }


def _full_results(router_id="lab-router"):
    return {
        "get_system_status": _env(router_id, sections={
            "resource": {
                "board-name": "RB5009UG+S+", "version": "7.15.3 (stable)",
                "architecture": "arm64", "uptime": "1w2d3h4m5s", "cpu-load": 4.5,
                "free-memory": 731906048, "total-memory": 1073741824,
                "free-hdd-space": 900000000, "total-hdd-space": 1000000000,
            },
            "identity": {"name": "lab-gw"},
            "license": {"level": "6"},
            "routerboard": {
                "model": "RB5009UG+S+", "firmware-type": "al64",
                "current-firmware": "7.15.3",
            },
            # health is present in the payload but excluded by contract
            "health": {"temperature": 42, "voltage": 24.2},
            "clock": {"date": "2026-07-15", "time": "12:00:00",
                      "time-zone-name": "Europe/Moscow"},
        }),
        "list_interfaces": _env(router_id, interfaces=[
            # deliberately unsorted; extra fields (status, mac-address) must drop
            {"name": "ether2", "type": "ether", "running": True, "disabled": False,
             "mtu": 1500, "status": "up", "mac-address": "AA:BB:CC:DD:EE:02"},
            {"name": "ether1", "type": "ether", "running": True, "disabled": False,
             "mtu": 1500, "status": "up", "mac-address": "AA:BB:CC:DD:EE:01"},
            {"name": "bridge1", "type": "bridge", "running": True, "disabled": False,
             "mtu": 1500},
        ], total=3, hasMore=False, offset=0, limit=100),
        "list_routes": _env(router_id, routes=[
            {"dst-address": "10.0.0.0/24", "gateway": "192.168.88.1", "distance": 1,
             "routing-table": "main", "active": True, "disabled": False},
            {"dst-address": "0.0.0.0/0", "gateway": "192.168.88.1", "distance": 1,
             "routing-table": "main", "active": True, "disabled": False},
        ], total=2, hasMore=False),
        "get_dns_settings": _env(router_id, settings={
            "servers": "1.1.1.1,8.8.8.8", "cache-size": 2048,
            "cache-max-ttl": "1w", "allow-remote-requests": True,
        }),
        "list_dhcp_servers": _env(router_id, servers=[
            {"name": "dhcp1", "interface": "bridge1", "address-pool": "pool1",
             "lease-time": "10m", "disabled": False},
        ], total=1, hasMore=False),
    }


def _collect_keys(value, keys):
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(k)
            _collect_keys(v, keys)
    elif isinstance(value, list):
        for v in value:
            _collect_keys(v, keys)


class ValidProjectionTest(unittest.TestCase):
    def test_full_payload_projects_exactly(self):
        result = mi.project_mikrotik_inventory(_full_results())
        self.assertEqual(result, {
            "contract": "mikromcp-renderer-v1.7.0",
            "coverage": "partial",
            "missing_capabilities": ["ip_addresses"],
            "unavailable_sections": [],
            "truncated_sections": [],
            "router_id": "lab-router",
            "system": {
                "resource": {
                    "board_name": "RB5009UG+S+", "version": "7.15.3 (stable)",
                    "architecture": "arm64", "uptime": "1w2d3h4m5s", "cpu_load": 4.5,
                    "free_memory": 731906048, "total_memory": 1073741824,
                    "free_hdd_space": 900000000, "total_hdd_space": 1000000000,
                },
                "identity": {"name": "lab-gw"},
                "license": {"level": "6"},
                "routerboard": {"model": "RB5009UG+S+", "firmware_type": "al64",
                                "current_firmware": "7.15.3"},
                "clock": {"date": "2026-07-15", "time": "12:00:00",
                          "time_zone_name": "Europe/Moscow"},
            },
            "interfaces": [   # sorted by name; unknown fields dropped
                {"name": "bridge1", "type": "bridge", "running": True,
                 "disabled": False, "mtu": 1500},
                {"name": "ether1", "type": "ether", "running": True,
                 "disabled": False, "mtu": 1500},
                {"name": "ether2", "type": "ether", "running": True,
                 "disabled": False, "mtu": 1500},
            ],
            "routes": [       # sorted by dst_address string
                {"dst_address": "0.0.0.0/0", "gateway": "192.168.88.1", "distance": 1,
                 "routing_table": "main", "active": True, "disabled": False},
                {"dst_address": "10.0.0.0/24", "gateway": "192.168.88.1", "distance": 1,
                 "routing_table": "main", "active": True, "disabled": False},
            ],
            "dns": {"cache_size": 2048, "cache_max_ttl": "1w",
                    "allow_remote_requests": True,
                    "servers": ["1.1.1.1", "8.8.8.8"]},
            "dhcp_servers": [
                {"name": "dhcp1", "interface": "bridge1", "address_pool": "pool1",
                 "lease_time": "10m", "disabled": False},
            ],
        })

    def test_scalar_types_preserved(self):
        result = mi.project_mikrotik_inventory(_full_results())
        self.assertIs(type(result["interfaces"][0]["mtu"]), int)
        self.assertIs(type(result["interfaces"][0]["running"]), bool)
        self.assertIs(type(result["dns"]["allow_remote_requests"]), bool)
        self.assertIs(type(result["routes"][0]["distance"]), int)
        self.assertIs(type(result["system"]["resource"]["cpu_load"]), float)
        self.assertEqual(result["system"]["resource"]["cpu_load"], 4.5)

    def test_dns_singular_server_key_accepted(self):
        results = _full_results()
        results["get_dns_settings"] = _env(settings={
            "server": "9.9.9.9", "cache-size": 100,
        })
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(result["dns"]["servers"], ["9.9.9.9"])

    def test_dns_null_servers_falls_back_to_singular_server(self):
        results = _full_results()
        results["get_dns_settings"] = _env(settings={
            "servers": None,
            "server": "9.9.9.9",
        })
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(result["dns"]["servers"], ["9.9.9.9"])

    def test_dns_servers_as_string_list_accepted(self):
        results = _full_results()
        results["get_dns_settings"] = _env(settings={
            "servers": ["1.1.1.1", "8.8.8.8"],
        })
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(result["dns"]["servers"], ["1.1.1.1", "8.8.8.8"])


class MissingSectionTest(unittest.TestCase):
    def test_missing_result_listed_and_key_absent(self):
        results = _full_results()
        del results["list_routes"]
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(result["unavailable_sections"], ["routes"])
        self.assertNotIn("routes", result)          # no fabricated empty list
        self.assertIn("interfaces", result)         # others still projected

    def test_multiple_missing_results_deterministic_order(self):
        results = _full_results()
        del results["list_routes"]
        del results["list_dhcp_servers"]
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(result["unavailable_sections"], ["routes", "dhcp_servers"])

    def test_mixed_subsection_and_toplevel_unavailable_order(self):
        results = _full_results()
        del results["get_system_status"]["structuredContent"]["sections"]["license"]
        del results["list_routes"]
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(result["unavailable_sections"], ["system.license", "routes"])

    def test_no_results_fails_closed(self):
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory({})
        self.assertEqual(c.exception.reason, "no_supported_results")
        self.assertEqual(c.exception.http_status, 400)

    def test_only_unknown_tools_fails_closed(self):
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory({"list_wifi_clients": _env(clients=[])})
        self.assertEqual(c.exception.reason, "no_supported_results")

    def test_unknown_tool_alongside_supported_is_ignored_unvalidated(self):
        results = _full_results()
        # garbage envelope for an unknown tool must not even be validated
        results["list_wifi_clients"] = {"isError": True}
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(result["unavailable_sections"], [])


class EnvelopeErrorTest(unittest.TestCase):
    def test_is_error_fails_closed_without_echo(self):
        results = _full_results()
        results["list_interfaces"] = {
            "isError": True,
            "content": [{"type": "text", "text": "Error [AUTH]: admin:hunter2 rejected"}],
        }
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory(results)
        self.assertEqual(c.exception.reason, "tool_error")
        self.assertEqual(c.exception.http_status, 502)  # malformed upstream payload
        self.assertNotIn("hunter2", str(c.exception))   # untrusted text never echoed

    def test_missing_structured_content_fails_closed(self):
        results = _full_results()
        results["list_interfaces"] = {"content": [{"type": "text", "text": "x"}]}
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory(results)
        self.assertEqual(c.exception.reason, "structured_content_not_object")
        self.assertEqual(c.exception.http_status, 502)

    def test_wrong_structured_content_type_fails_closed(self):
        for bad in ("text", ["list"], 5):
            results = _full_results()
            results["list_interfaces"] = {"structuredContent": bad}
            with self.assertRaises(mi.MikrotikProjectionError) as c:
                mi.project_mikrotik_inventory(results)
            self.assertEqual(c.exception.reason, "structured_content_not_object")

    def test_result_envelope_not_object_fails_closed(self):
        results = _full_results()
        results["list_interfaces"] = "oops"
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory(results)
        self.assertEqual(c.exception.reason, "result_not_object")

    def test_results_not_mapping_fails_closed(self):
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory([("list_interfaces", {})])
        self.assertEqual(c.exception.reason, "results_not_object")
        self.assertEqual(c.exception.http_status, 400)  # caller error, not upstream

    def test_error_type_is_value_error_with_machine_contract(self):
        # Routes/callers catch ValueError and map reason/http_status — pin both.
        self.assertTrue(issubclass(mi.MikrotikProjectionError, ValueError))
        exc = mi.MikrotikProjectionError("some_reason", "tool=x")
        self.assertEqual(exc.http_status, 502)          # default: upstream payload


class RouterIdTest(unittest.TestCase):
    def test_mismatch_fails_closed(self):
        results = _full_results()
        results["list_routes"] = _env("other-router", routes=[])
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory(results)
        self.assertEqual(c.exception.reason, "router_id_mismatch")

    def test_missing_empty_or_nonstring_fails_closed(self):
        for bad_structured in (
            {"interfaces": []},                       # routerId missing
            {"routerId": "", "interfaces": []},       # empty
            {"routerId": "   ", "interfaces": []},    # whitespace-only
            {"routerId": 42, "interfaces": []},       # non-string
        ):
            results = _full_results()
            results["list_interfaces"] = {"structuredContent": bad_structured}
            with self.assertRaises(mi.MikrotikProjectionError) as c:
                mi.project_mikrotik_inventory(results)
            self.assertEqual(c.exception.reason, "router_id_invalid")


class ContainerTypeTest(unittest.TestCase):
    def test_list_container_wrong_type_fails_closed(self):
        results = _full_results()
        results["list_interfaces"] = _env(interfaces={"name": "ether1"})
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory(results)
        self.assertEqual(c.exception.reason, "container_not_list")

    def test_sections_container_wrong_type_fails_closed(self):
        results = _full_results()
        results["get_system_status"] = _env(sections=["resource"])
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory(results)
        self.assertEqual(c.exception.reason, "container_not_object")

    def test_dns_settings_wrong_type_fails_closed(self):
        results = _full_results()
        results["get_dns_settings"] = _env(settings=5)
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory(results)
        self.assertEqual(c.exception.reason, "container_not_object")

    def test_non_object_list_item_fails_closed(self):
        results = _full_results()
        results["list_routes"] = _env(routes=[{"dst-address": "10.0.0.0/24"}, "junk"])
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory(results)
        self.assertEqual(c.exception.reason, "record_not_object")

    def test_complex_value_in_whitelisted_field_fails_closed(self):
        results = _full_results()
        results["list_interfaces"] = _env(interfaces=[
            {"name": "ether1", "mtu": {"nested": 1}},
        ])
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory(results)
        self.assertEqual(c.exception.reason, "field_complex_type")

    def test_non_finite_number_fails_closed(self):
        for bad in (math.nan, math.inf, -math.inf):
            results = _full_results()
            results["list_interfaces"] = _env(interfaces=[
                {"name": "ether1", "mtu": bad},
            ])
            with self.assertRaises(mi.MikrotikProjectionError) as c:
                mi.project_mikrotik_inventory(results)
            self.assertEqual(c.exception.reason, "field_non_finite")
            self.assertNotIn(str(bad), str(c.exception))

    def test_dns_servers_wrong_type_fails_closed(self):
        for bad in (["1.1.1.1", 2], 17, {"a": 1}):
            results = _full_results()
            results["get_dns_settings"] = _env(settings={"servers": bad})
            with self.assertRaises(mi.MikrotikProjectionError) as c:
                mi.project_mikrotik_inventory(results)
            self.assertEqual(c.exception.reason, "dns_servers_invalid")


class CapsTest(unittest.TestCase):
    def _interfaces(self, count):
        return [{"name": f"ether{i:04d}", "type": "ether", "running": True,
                 "disabled": False, "mtu": 1500} for i in range(count)]

    def test_interfaces_cap_128_marks_truncated(self):
        results = _full_results()
        results["list_interfaces"] = _env(interfaces=self._interfaces(130))
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(len(result["interfaces"]), 128)
        self.assertEqual(result["truncated_sections"], ["interfaces"])

    def test_has_more_marks_truncated_without_local_cut(self):
        results = _full_results()
        results["list_interfaces"] = _env(interfaces=self._interfaces(2), hasMore=True)
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(len(result["interfaces"]), 2)
        self.assertIn("interfaces", result["truncated_sections"])

    def test_routes_cap_256(self):
        results = _full_results()
        results["list_routes"] = _env(routes=[
            {"dst-address": f"10.{i // 256}.{i % 256}.0/24", "gateway": "g",
             "distance": 1, "routing-table": "main", "active": True,
             "disabled": False} for i in range(300)
        ])
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(len(result["routes"]), 256)
        self.assertIn("routes", result["truncated_sections"])

    def test_dhcp_servers_cap_128(self):
        results = _full_results()
        results["list_dhcp_servers"] = _env(servers=[
            {"name": f"dhcp{i:04d}", "interface": "bridge1",
             "address-pool": "p", "lease-time": "10m", "disabled": False}
            for i in range(130)
        ])
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(len(result["dhcp_servers"]), 128)
        self.assertIn("dhcp_servers", result["truncated_sections"])

    def test_dns_servers_cap_32(self):
        results = _full_results()
        results["get_dns_settings"] = _env(settings={
            "servers": ",".join(f"10.0.0.{i}" for i in range(40)),
        })
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(len(result["dns"]["servers"]), 32)
        self.assertIn("dns", result["truncated_sections"])

    def test_strings_clipped_to_256(self):
        results = _full_results()
        results["get_system_status"]["structuredContent"]["sections"]["resource"][
            "version"] = "v" * 300
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(len(result["system"]["resource"]["version"]), 256)

    def test_router_id_clipped_to_256(self):
        result = mi.project_mikrotik_inventory(_full_results(router_id="R" * 300))
        self.assertEqual(len(result["router_id"]), 256)

    def test_dns_server_entries_clipped_to_256(self):
        results = _full_results()
        results["get_dns_settings"] = _env(settings={"servers": "s" * 300})
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual([len(s) for s in result["dns"]["servers"]], [256])
        self.assertNotIn("dns", result["truncated_sections"])

    def test_cap_keeps_sorted_prefix_regardless_of_input_order(self):
        # Sort must happen BEFORE the cap: the surviving 128 records are the
        # lexicographically-first ones, whatever order the payload arrived in.
        straight, reversed_ = _full_results(), _full_results()
        straight["list_interfaces"] = _env(interfaces=self._interfaces(130))
        reversed_["list_interfaces"] = _env(
            interfaces=list(reversed(self._interfaces(130))))
        out_a = mi.project_mikrotik_inventory(straight)
        out_b = mi.project_mikrotik_inventory(reversed_)
        self.assertEqual(out_a, out_b)
        self.assertEqual(out_a["interfaces"][0]["name"], "ether0000")
        self.assertEqual(out_a["interfaces"][-1]["name"], "ether0127")

    def test_has_more_marks_every_section_kind(self):
        # The contract clause is generic: hasMore=true marks the section
        # truncated for ALL five tools, not only the paginated list tools.
        cases = (
            ("get_system_status", "system"),
            ("list_interfaces", "interfaces"),
            ("list_routes", "routes"),
            ("get_dns_settings", "dns"),
            ("list_dhcp_servers", "dhcp_servers"),
        )
        for tool, section in cases:
            results = _full_results()
            results[tool]["structuredContent"]["hasMore"] = True
            result = mi.project_mikrotik_inventory(results)
            self.assertEqual(result["truncated_sections"], [section],
                             f"hasMore not honored for {tool}")

    def test_has_more_requires_json_true_not_truthiness(self):
        results = _full_results()
        results["list_interfaces"]["structuredContent"]["hasMore"] = "false"
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(result["truncated_sections"], [])

    def test_two_truncated_sections_deterministic_order(self):
        results = _full_results()
        results["list_interfaces"] = _env(interfaces=self._interfaces(130))
        results["list_routes"] = _env(routes=[
            {"dst-address": f"10.{i // 256}.{i % 256}.0/24", "gateway": "g",
             "distance": 1, "routing-table": "main", "active": True,
             "disabled": False} for i in range(300)
        ])
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(result["truncated_sections"], ["interfaces", "routes"])


class StableOrderingTest(unittest.TestCase):
    def test_shuffled_input_lists_produce_identical_output(self):
        straight = _full_results()
        shuffled = _full_results()
        for tool, key in (("list_interfaces", "interfaces"),
                          ("list_routes", "routes"),
                          ("list_dhcp_servers", "servers")):
            shuffled[tool]["structuredContent"][key].reverse()
        self.assertEqual(
            mi.project_mikrotik_inventory(straight),
            mi.project_mikrotik_inventory(shuffled),
        )

    def test_sort_distinguishes_equal_text_different_scalar_types(self):
        forward = _full_results()
        reverse = _full_results()
        records = [
            {"name": "same", "mtu": 1},
            {"name": "same", "mtu": "1"},
        ]
        forward["list_interfaces"] = _env(interfaces=records)
        reverse["list_interfaces"] = _env(interfaces=list(reversed(records)))
        self.assertEqual(
            mi.project_mikrotik_inventory(forward),
            mi.project_mikrotik_inventory(reverse),
        )


class PoisonedPayloadTest(unittest.TestCase):
    _SENTINELS = (
        "SENTINEL-PASSWORD-XYZZY", "SENTINEL-TOKEN-XYZZY", "SENTINEL-KEY-XYZZY",
        "SENTINEL-COMMENT-XYZZY", "SENTINEL-SERIAL-XYZZY", "SENTINEL-SWID-XYZZY",
        "SENTINEL-CERT-XYZZY", "SENTINEL-SCRIPT-XYZZY", "SENTINEL-EXPORT-XYZZY",
        "SENTINEL-HEALTH-XYZZY",
    )
    _FORBIDDEN_KEYS = {
        "password", "token", "private_key", "private-key", "comment",
        "serial-number", "serial_number", "software-id", "software_id",
        "health", "certificate", "certificates", "script", "scripts",
        "export", "credentials", "secret",
    }

    def _poisoned(self):
        results = _full_results()
        sections = results["get_system_status"]["structuredContent"]["sections"]
        sections["resource"].update({
            "serial-number": "SENTINEL-SERIAL-XYZZY",
            "software-id": "SENTINEL-SWID-XYZZY",
        })
        sections["identity"].update({
            "password": "SENTINEL-PASSWORD-XYZZY",
            "token": "SENTINEL-TOKEN-XYZZY",
            "private-key": "SENTINEL-KEY-XYZZY",
        })
        sections["routerboard"]["serial-number"] = "SENTINEL-SERIAL-XYZZY"
        sections["health"] = {"probe": "SENTINEL-HEALTH-XYZZY"}
        results["list_interfaces"]["structuredContent"]["interfaces"][0].update({
            "comment": "SENTINEL-COMMENT-XYZZY",
            "credentials": "SENTINEL-PASSWORD-XYZZY",
        })
        results["list_routes"]["structuredContent"]["routes"][0][
            "comment"] = "SENTINEL-COMMENT-XYZZY"
        results["get_dns_settings"]["structuredContent"]["settings"].update({
            "certificate": "SENTINEL-CERT-XYZZY",
        })
        results["list_dhcp_servers"]["structuredContent"]["servers"][0].update({
            "script": "SENTINEL-SCRIPT-XYZZY",
            "export": "SENTINEL-EXPORT-XYZZY",
        })
        return results

    def test_no_sentinel_value_survives_projection(self):
        dumped = json.dumps(mi.project_mikrotik_inventory(self._poisoned()))
        for sentinel in self._SENTINELS:
            self.assertNotIn(sentinel, dumped)

    def test_no_forbidden_key_survives_projection(self):
        result = mi.project_mikrotik_inventory(self._poisoned())
        keys: set = set()
        _collect_keys(result, keys)
        self.assertFalse(keys & self._FORBIDDEN_KEYS,
                         f"forbidden keys leaked: {keys & self._FORBIDDEN_KEYS}")

    def test_poisoned_payload_still_projects_whitelist(self):
        result = mi.project_mikrotik_inventory(self._poisoned())
        self.assertEqual(result["system"]["identity"], {"name": "lab-gw"})
        self.assertEqual(result["interfaces"][0]["name"], "bridge1")


class SystemErrorSubsectionTest(unittest.TestCase):
    def test_error_subsection_marked_unavailable_without_echo(self):
        results = _full_results()
        results["get_system_status"]["structuredContent"]["sections"]["resource"] = {
            "_error": "Failed to fetch: admin:hunter2@192.168.88.1 refused",
        }
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(result["unavailable_sections"], ["system.resource"])
        self.assertNotIn("resource", result["system"])
        dumped = json.dumps(result)
        self.assertNotIn("hunter2", dumped)          # credentials from the error text
        self.assertNotIn("Failed to fetch", dumped)  # the error text itself
        self.assertNotIn("_error", dumped)

    def test_missing_subsection_listed_unavailable(self):
        results = _full_results()
        del results["get_system_status"]["structuredContent"]["sections"]["license"]
        result = mi.project_mikrotik_inventory(results)
        self.assertEqual(result["unavailable_sections"], ["system.license"])
        self.assertNotIn("license", result["system"])

    def test_subsection_wrong_type_fails_closed(self):
        results = _full_results()
        results["get_system_status"]["structuredContent"]["sections"]["identity"] = "x"
        with self.assertRaises(mi.MikrotikProjectionError) as c:
            mi.project_mikrotik_inventory(results)
        self.assertEqual(c.exception.reason, "container_not_object")


class PurityTest(unittest.TestCase):
    def test_module_imports_only_pure_stdlib_typing(self):
        source = Path(mi.__file__).read_text(encoding="utf-8")
        modules: set[str] = set()
        banned_calls: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module or "")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                # import-free I/O escapes that the runtime patches can't see
                if node.func.id in {"open", "eval", "exec", "__import__", "compile"}:
                    banned_calls.add(node.func.id)
        self.assertLessEqual(
            modules, {"__future__", "collections.abc", "heapq", "math", "typing"},
            f"unexpected imports: {modules}")
        self.assertFalse(banned_calls, f"banned builtin calls: {banned_calls}")

    def test_projection_makes_no_network_or_process_calls(self):
        deny = AssertionError("pure projector attempted I/O")
        with mock.patch("socket.socket", side_effect=deny), \
                mock.patch("socket.create_connection", side_effect=deny), \
                mock.patch("subprocess.Popen", side_effect=deny), \
                mock.patch("subprocess.run", side_effect=deny), \
                mock.patch("builtins.open", side_effect=deny):
            result = mi.project_mikrotik_inventory(_full_results())
        self.assertEqual(result["router_id"], "lab-router")


class EncodingTest(unittest.TestCase):
    """AGENTS.md: UTF-8 without BOM, LF only. The repo-wide mojibake gate scans
    backend/app; this pins the same property for BOTH new Phase 7A files."""

    def test_no_bom_no_crlf(self):
        for path in (Path(mi.__file__), Path(__file__)):
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM in {path.name}")
            self.assertNotIn(b"\r\n", raw, f"CRLF in {path.name}")


if __name__ == "__main__":
    unittest.main()
