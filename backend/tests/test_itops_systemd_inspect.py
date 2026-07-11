"""Phase 4a — systemd service inspect helpers: strict unit-name allowlist, fixed
`systemctl show` command, and the TYPED projection (only the fixed non-secret fields
survive; raw/unknown keys are dropped)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.it_ops import systemd_inspect as si  # noqa: E402


class UnitNameTest(unittest.TestCase):
    def test_accepts_real_service_units(self):
        for u in ("netdata.service", "silero-tts.service", "getty@tty1.service",
                  "systemd-journald@netdata.service", "user@1001.service", "cron.service",
                  "docker.service", "a.service"):
            self.assertTrue(si.unit_name_ok(u), u)

    def test_rejects_non_service_and_malformed(self):
        for u in ("", "   ", "netdata", "netdata.socket", "netdata.timer",
                  "-bad.service", ".service", "net data.service", "net\ndata.service"):
            self.assertFalse(si.unit_name_ok(u), u)

    def test_rejects_injection_and_traversal(self):
        for u in ("netdata.service; rm -rf /", "netdata.service && id", "$(reboot).service",
                  "`id`.service", "a|b.service", "../../etc/passwd.service", "a/b.service",
                  "a..b.service", "unit.service\x00"):
            self.assertFalse(si.unit_name_ok(u), u)

    def test_rejects_over_long(self):
        self.assertFalse(si.unit_name_ok("a" * 130 + ".service"))


class ShowCommandTest(unittest.TestCase):
    def test_fixed_readonly_show_argv(self):
        argv = si.show_remote_command("netdata.service")
        self.assertEqual(argv[:2], ["systemctl", "show"])        # read-only subcommand only
        self.assertIn("-p", argv)
        self.assertEqual(argv[-1], "netdata.service")            # unit is a single argv token
        # exactly the fixed property set, nothing model-driven
        props = argv[argv.index("-p") + 1]
        self.assertEqual(props, ",".join(si.SHOW_PROPERTIES))
        # no state-changing / log / unit-file subcommand is present as its own token
        # (substring checks would false-positive on NRestarts/ExecMainStatus)
        state_changing = {"start", "stop", "restart", "reload", "enable", "disable", "kill",
                          "status", "cat", "edit", "set-property", "mask"}
        self.assertEqual([t for t in argv if t in state_changing], [])
        self.assertTrue(all("journalctl" not in t and ";" not in t and "&" not in t and "|" not in t
                            for t in argv))


class ProjectionTest(unittest.TestCase):
    RAW = (
        "Id=netdata.service\n"
        "LoadState=loaded\n"
        "ActiveState=active\n"
        "SubState=running\n"
        "UnitFileState=enabled\n"
        "MainPID=1238\n"
        "ExecMainStatus=0\n"
        "NRestarts=0\n"
        "FragmentPath=/usr/lib/systemd/system/netdata.service\n"
        # a property OUTSIDE the fixed set (as if it leaked in) — must be dropped:
        "Environment=SECRET_TOKEN=abc123\n"
        "ExecStart={ path=/usr/sbin/netdata ; argv[]=...; password=hunter2 }\n"
    )

    def test_only_fixed_fields_survive_with_types(self):
        out = si.parse_show_output(self.RAW)
        self.assertEqual(out, {
            "id": "netdata.service", "load_state": "loaded", "active_state": "active",
            "sub_state": "running", "unit_file_state": "enabled",
            "main_pid": 1238, "exec_main_status": 0, "n_restarts": 0,
            "fragment_path": "/usr/lib/systemd/system/netdata.service",
        })
        # numeric fields are ints, not strings
        self.assertIsInstance(out["main_pid"], int)
        self.assertIsInstance(out["n_restarts"], int)

    def test_unknown_keys_and_secrets_are_dropped(self):
        out = si.parse_show_output(self.RAW)
        blob = repr(out)
        for leak in ("Environment", "SECRET_TOKEN", "abc123", "ExecStart", "password", "hunter2"):
            self.assertNotIn(leak, blob)

    def test_non_integer_numeric_becomes_none_not_string(self):
        out = si.parse_show_output("MainPID=notanumber\nActiveState=active\n")
        self.assertIsNone(out["main_pid"])
        self.assertEqual(out["active_state"], "active")

    def test_empty_output_is_empty_projection(self):
        self.assertEqual(si.parse_show_output(""), {})


if __name__ == "__main__":
    unittest.main()
