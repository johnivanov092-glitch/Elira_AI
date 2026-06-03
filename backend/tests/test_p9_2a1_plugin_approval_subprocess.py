"""P9.2-FIXUP — plugin lockdown + admin-authorized approve-and-retry subprocess path.

A freshly discovered plugin is forbidden + disabled + unclassified: POST /plugins/run
is blocked by the kernel and the subprocess never runs. Only after an admin classifies
it via the Tool API (PATCH permission=require_approval, enabled=true,
policy_classified=true) does the approval flow apply — then approve + retry with the
same run_id actually runs the plugin in a child process and returns its real result.
"""
from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path
from unittest import mock

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.application.tool_registry.runtime as reg  # noqa: E402
import app.infrastructure.plugins.plugin_system as psys  # noqa: E402
from app.api.routes.agent_monitor_routes import router as monitor_router  # noqa: E402
from app.api.routes.skills_extra_routes import router as extra_router  # noqa: E402
from app.api.routes.tool_registry_routes import router as tool_router  # noqa: E402

_PLUGIN_SRC = textwrap.dedent("""\
    def run(args: dict) -> dict:
        return {"ok": True, "echoed": args.get("value", "default"), "via": "subprocess"}
""")


def _make_app() -> FastAPI:
    application = FastAPI()
    application.include_router(tool_router)
    application.include_router(monitor_router)
    application.include_router(extra_router)
    return application


client = TestClient(_make_app())


class PluginApproveRetrySubprocessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.name = f"p92a1_plug_{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        try:
            reg.delete_tool(self.name)
        except Exception:
            pass

    def _load_plugin(self, tmp: Path) -> None:
        (tmp / f"{self.name}.py").write_text(_PLUGIN_SRC, encoding="utf-8")
        manifest = {
            "name": self.name,
            "version": "1.0.0",
            "capabilities": ["testing"],
            "category": "testing",
            "enabled": True,
        }
        (tmp / f"{self.name}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with mock.patch.object(psys, "PLUGINS_DIR", tmp):
            psys.load_plugins()

    def _classify_for_approval(self) -> None:
        """Admin action: classify + enable + require_approval via the Tool API."""
        rp = client.patch(
            f"/api/agent-os/tools/{self.name}",
            json={"permission": "require_approval", "enabled": True, "policy_classified": True},
        )
        self.assertEqual(rp.status_code, 200, rp.text)

    def test_fresh_plugin_blocked_then_admin_classify_enables_approval_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._load_plugin(Path(tmpdir))

            # A freshly loaded plugin is fail-closed: forbidden + disabled + unclassified.
            spec = reg.get_tool(self.name)
            self.assertIsNotNone(spec)
            assert spec is not None
            self.assertEqual(spec["permission"], "forbidden")
            self.assertFalse(spec["enabled"])
            self.assertFalse(spec["policy_classified"])

            # 0) before classification → blocked by the kernel, subprocess NOT run
            r0 = client.post("/api/extra/plugins/run",
                             json={"name": self.name, "args": {"value": "hi"}})
            self.assertEqual(r0.status_code, 200)
            d0 = r0.json()
            self.assertFalse(d0.get("ok", True), "unclassified plugin must be blocked")
            self.assertNotIn("approval_id", d0, "blocked, not an approval gate")
            self.assertNotIn("via", d0, "subprocess must not have run")

            # 1) admin classifies → require_approval + enabled + classified
            self._classify_for_approval()

            # 2) now first call → waiting_approval, subprocess NOT run
            r1 = client.post("/api/extra/plugins/run",
                             json={"name": self.name, "args": {"value": "hi"}})
            self.assertEqual(r1.status_code, 200)
            d1 = r1.json()
            run_id = d1["run_id"]
            self.assertTrue(run_id, "plugins/run must return a stable run_id")
            self.assertFalse(d1.get("ok", True), "must wait for approval, not run")
            self.assertIn("approval_id", d1)
            self.assertNotIn("via", d1, "subprocess must not have run yet")

            # 3) approve
            ra = client.post(f"/api/agent-os/approvals/{d1['approval_id']}/approve")
            self.assertEqual(ra.status_code, 200)

            # 4) retry with the SAME run_id + SAME args → real subprocess executes
            r2 = client.post("/api/extra/plugins/run",
                             json={"name": self.name, "args": {"value": "hi"}, "run_id": run_id})
            self.assertEqual(r2.status_code, 200)
            d2 = r2.json()
            self.assertTrue(d2.get("ok"), f"expected real execution after approve, got {d2}")
            self.assertEqual(d2.get("echoed"), "hi")
            self.assertEqual(d2.get("via"), "subprocess",
                             "result must come from the real plugin child process")

    def test_plugin_run_different_args_after_approval_blocks_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._load_plugin(Path(tmpdir))
            self._classify_for_approval()

            d1 = client.post("/api/extra/plugins/run",
                             json={"name": self.name, "args": {"value": "hi"}}).json()
            client.post(f"/api/agent-os/approvals/{d1['approval_id']}/approve")

            # tampered args + same run_id must not reuse the approval
            d2 = client.post("/api/extra/plugins/run",
                             json={"name": self.name, "args": {"value": "TAMPERED"}, "run_id": d1["run_id"]}).json()
            self.assertFalse(d2.get("ok", True))
            self.assertIn("approval_id", d2)
            self.assertNotIn("via", d2, "subprocess must not run for unapproved args")


if __name__ == "__main__":
    unittest.main()
