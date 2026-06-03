"""P9.2A1 debt — real plugin approve-and-retry through the kernel subprocess path.

Plugins are classified require_approval (untrusted subprocess code). This drives
the full HTTP flow end to end: POST /plugins/run returns waiting_approval WITHOUT
running the subprocess; the approval is granted; the retry with the same run_id
actually runs the plugin in a child process and returns its real result.
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

    def test_plugin_run_requires_approval_then_executes_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._load_plugin(Path(tmpdir))

            spec = reg.get_tool(self.name)
            self.assertIsNotNone(spec)
            assert spec is not None
            self.assertEqual(spec["permission"], "require_approval",
                             "P9.2A1: plugins must be classified require_approval")

            # 1) first call → waiting_approval, subprocess NOT run
            r1 = client.post("/api/extra/plugins/run",
                             json={"name": self.name, "args": {"value": "hi"}})
            self.assertEqual(r1.status_code, 200)
            d1 = r1.json()
            run_id = d1["run_id"]
            self.assertTrue(run_id, "plugins/run must return a stable run_id")
            self.assertFalse(d1.get("ok", True), "must wait for approval, not run")
            self.assertIn("approval_id", d1)
            self.assertNotIn("via", d1, "subprocess must not have run yet")

            # 2) approve
            ra = client.post(f"/api/agent-os/approvals/{d1['approval_id']}/approve")
            self.assertEqual(ra.status_code, 200)

            # 3) retry with the SAME run_id + SAME args → real subprocess executes
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
