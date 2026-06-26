"""P9.2-FIXUP — fail-closed ToolSpec, dynamic-tool lockdown, and enforcement.

Covers the fixup contract:
  * a tool with no spec, or an unclassified spec, never reaches the provider;
  * SSH/MCP tools without classification are blocked (and SSH specs carry scopes);
  * plugin reload refreshes metadata but never resets admin policy;
  * plugin on_start / chat-trigger / autopipeline DIRECT execution is disabled;
  * Tool API rejects an invalid permission/scope with HTTP 400;
  * allowed_tools blocks a tool per-call through the executor;
  * allowed_scopes round-trips through the agent-limit API;
  * fail-closed blocks emit tool.invalid_spec.
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

import app.application.monitoring.runtime as mon  # noqa: E402
import app.application.tool_registry.runtime as reg  # noqa: E402
import app.infrastructure.plugins.plugin_system as psys  # noqa: E402
from app.application.agent_kernel.executor import (  # noqa: E402
    ToolExecutionRequest,
    execute_tool,
)
from app.api.routes.agent_monitor_routes import router as monitor_router  # noqa: E402
from app.api.routes.skills_extra_routes import router as extra_router  # noqa: E402
from app.api.routes.tool_registry_routes import router as tool_router  # noqa: E402


def _make_app() -> FastAPI:
    application = FastAPI()
    application.include_router(tool_router)
    application.include_router(monitor_router)
    application.include_router(extra_router)
    return application


client = TestClient(_make_app())

_PLUGIN_SRC = textwrap.dedent("""\
    def on_start():
        return "STARTED"

    def run(args: dict) -> dict:
        return {"ok": True, "echoed": args.get("value", "default"), "via": "subprocess"}
""")


# ── 1. Fail-closed: spec None / unclassified never reach the provider ──────────

class FailClosedSpecTest(unittest.TestCase):
    def _exec(self, tool_name: str, agent_id: str = "p92fix-agent"):
        self.calls: list[str] = []

        def _dispatch(n: str, a: dict) -> dict:
            self.calls.append(n)
            return {"ok": True, "ran": True}

        return execute_tool(
            ToolExecutionRequest(
                run_id="r", agent_id=agent_id, project_scope_id="",
                tool_name=tool_name, args={}, source="test",
            ),
            dispatch_fn=_dispatch,
        )

    def test_spec_none_blocked_before_provider(self) -> None:
        res = self._exec(f"no_such_tool_{uuid.uuid4().hex[:8]}")
        self.assertEqual(res.status, "blocked")
        self.assertEqual(res.output.get("error"), "unknown_toolspec")
        self.assertEqual(self.calls, [], "provider must not be reached without a spec")

    def test_unclassified_blocked_before_provider(self) -> None:
        name = f"p92fix_unclass_{uuid.uuid4().hex[:8]}"
        reg.register_tool(name=name, handler=lambda a: {"ok": True}, permission="auto", source="builtin")
        # Force unclassified (simulates a plugin/MCP/migrated row reaching the kernel).
        reg.update_tool(name, {"policy_classified": False})
        try:
            res = self._exec(name)
            self.assertEqual(res.status, "blocked")
            self.assertEqual(res.output.get("error"), "unclassified_tool")
            self.assertEqual(self.calls, [], "unclassified tool must not reach the provider")
        finally:
            reg.delete_tool(name)

    def test_invalid_spec_event_emitted_for_unclassified(self) -> None:
        name = f"p92fix_evt_{uuid.uuid4().hex[:8]}"
        reg.register_tool(name=name, handler=lambda a: {"ok": True}, permission="auto", source="builtin")
        reg.update_tool(name, {"policy_classified": False})
        events: list[tuple] = []

        def _rec(*, event_type, payload, **kw):  # matches emit_event kwargs
            events.append((event_type, payload))
            return {}

        try:
            with mock.patch("app.application.event_bus.runtime.emit_event", _rec):
                execute_tool(
                    ToolExecutionRequest(
                        run_id="r", agent_id="a", project_scope_id="",
                        tool_name=name, args={}, source="test",
                    ),
                    dispatch_fn=lambda n, a: {"ok": True},
                )
            self.assertTrue(
                any(et == "tool.invalid_spec" for et, _ in events),
                f"expected a tool.invalid_spec event, got {[e[0] for e in events]}",
            )
        finally:
            reg.delete_tool(name)


# ── 2. SSH / MCP classification ────────────────────────────────────────────────

class SshMcpClassificationTest(unittest.TestCase):
    def test_ssh_specs_have_required_scopes(self) -> None:
        from app.application.tool_registry.builtins import _build_ssh_tools
        specs = {t["name"]: t for t in _build_ssh_tools()}
        self.assertEqual(specs["ssh_list_hosts"]["scopes"], ["net.outbound"])
        self.assertEqual(specs["ssh_read"]["scopes"], ["net.outbound", "fs.read"])
        self.assertEqual(specs["ssh_run"]["scopes"], ["net.outbound", "shell.exec"])
        self.assertEqual(specs["ssh_write"]["scopes"], ["net.outbound", "fs.write"])
        # SSH ships in the trusted builtin set → seeded classified + enabled.
        for name in specs:
            self.assertEqual(specs[name]["source"], "ssh")

    def test_seeded_ssh_tool_is_classified(self) -> None:
        reg.seed_builtin_tools()
        spec = reg.get_tool("ssh_run")
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertTrue(spec["policy_classified"], "SSH tools must seed classified")
        self.assertEqual(sorted(spec["scopes"]), ["net.outbound", "shell.exec"])

    def test_mcp_spec_is_failclosed_and_blocked(self) -> None:
        name = f"mcpsrv__tool_{uuid.uuid4().hex[:8]}"
        reg.register_dynamic_tool(name, lambda a: {"ok": True}, source="mcp")
        try:
            spec = reg.get_tool(name)
            assert spec is not None
            self.assertEqual(spec["permission"], "forbidden")
            self.assertFalse(spec["enabled"])
            self.assertFalse(spec["policy_classified"])

            calls: list[str] = []
            res = execute_tool(
                ToolExecutionRequest(
                    run_id="r", agent_id="a", project_scope_id="",
                    tool_name=name, args={}, source="code_agent",
                ),
                dispatch_fn=lambda n, a: calls.append(n) or {"ok": True},
            )
            self.assertEqual(res.status, "blocked")
            self.assertEqual(calls, [], "unclassified MCP tool must not reach the provider")
        finally:
            reg.delete_tool(name)


# ── 3. Plugin reload preserves admin policy ────────────────────────────────────

class PluginReloadPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.name = f"p92fix_reload_{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        try:
            reg.delete_tool(self.name)
        except Exception:
            pass

    def _write_plugin(self, tmp: Path) -> None:
        (tmp / f"{self.name}.py").write_text(_PLUGIN_SRC, encoding="utf-8")
        manifest = {"name": self.name, "version": "1.0.0", "category": "testing", "enabled": True}
        (tmp / f"{self.name}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_reload_does_not_reset_admin_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            self._write_plugin(tmp)
            with mock.patch.object(psys, "PLUGINS_DIR", tmp):
                psys.load_plugins()

                # Admin classifies the plugin (the only way it can ever run).
                reg.update_tool(self.name, {
                    "permission": "require_approval",
                    "enabled": True,
                    "policy_classified": True,
                    "scopes": ["shell.exec"],
                })

                # Reload (e.g. a new plugin appeared) must NOT reset that policy.
                psys.reload_plugins()

            spec = reg.get_tool(self.name)
            assert spec is not None
            self.assertEqual(spec["permission"], "require_approval", "reload reset permission")
            self.assertTrue(spec["enabled"], "reload reset enabled")
            self.assertTrue(spec["policy_classified"], "reload reset classification")
            self.assertEqual(spec["scopes"], ["shell.exec"], "reload reset scopes")


# ── 4. Direct execution paths disabled (on_start / chat / autopipeline) ─────────

class DirectExecutionDisabledTest(unittest.TestCase):
    def test_plugin_on_start_not_run_on_load(self) -> None:
        name = f"p92fix_onstart_{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / f"{name}.py").write_text(_PLUGIN_SRC, encoding="utf-8")
            manifest = {"name": name, "version": "1.0.0", "category": "testing",
                        "enabled": True, "hooks": ["on_start"]}
            (tmp / f"{name}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            try:
                with mock.patch.object(psys, "PLUGINS_DIR", tmp), \
                     mock.patch.object(psys, "_run_plugin_subprocess") as sub:
                    psys.load_plugins()
                self.assertEqual(sub.call_count, 0, "on_start must NOT run a subprocess at load")
            finally:
                reg.delete_tool(name)

    def test_autopipeline_plugin_task_does_not_execute_directly(self) -> None:
        from app.application.autopipeline import runtime as autopipeline
        name = f"p92fix_autop_{uuid.uuid4().hex[:8]}"
        # A discovered-but-unclassified plugin spec: kernel must block it.
        reg.register_dynamic_tool(name, lambda a: {"ok": True}, source="plugin", scopes=["shell.exec"])
        try:
            with mock.patch.object(psys, "run_plugin") as rp:
                result = autopipeline._execute_task("plugin", {"plugin_name": name, "args": {}})
            self.assertEqual(rp.call_count, 0, "autopipeline must not run_plugin directly")
            self.assertFalse(result.get("ok", True), "blocked plugin task must not report ok")
        finally:
            reg.delete_tool(name)


# ── 5. Tool API validation → 400 ───────────────────────────────────────────────

class ToolApiValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.name = f"p92fix_api_{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        try:
            reg.delete_tool(self.name)
        except Exception:
            pass

    def test_register_invalid_permission_400(self) -> None:
        r = client.post("/api/agent-os/tools", json={"name": self.name, "permission": "bogus"})
        self.assertEqual(r.status_code, 400, r.text)

    def test_register_invalid_scope_400(self) -> None:
        r = client.post("/api/agent-os/tools",
                        json={"name": self.name, "permission": "auto", "scopes": ["bogus.scope"]})
        self.assertEqual(r.status_code, 400, r.text)

    def test_patch_invalid_permission_400(self) -> None:
        reg.register_tool(name=self.name, handler=lambda a: {"ok": True}, permission="auto", source="custom")
        r = client.patch(f"/api/agent-os/tools/{self.name}", json={"permission": "nope"})
        self.assertEqual(r.status_code, 400, r.text)

    def test_patch_invalid_scope_400(self) -> None:
        reg.register_tool(name=self.name, handler=lambda a: {"ok": True}, permission="auto", source="custom")
        r = client.patch(f"/api/agent-os/tools/{self.name}", json={"scopes": ["nope.scope"]})
        self.assertEqual(r.status_code, 400, r.text)

    def test_patch_classify_atomically(self) -> None:
        reg.register_tool(name=self.name, handler=lambda a: {"ok": True}, permission="auto",
                          source="custom", enabled=False, policy_classified=False)
        r = client.patch(f"/api/agent-os/tools/{self.name}", json={
            "permission": "require_approval", "scopes": ["fs.read"],
            "side_effect": True, "idempotent": False,
            "enabled": True, "policy_classified": True,
        })
        self.assertEqual(r.status_code, 200, r.text)
        spec = reg.get_tool(self.name)
        assert spec is not None
        self.assertEqual(spec["permission"], "require_approval")
        self.assertEqual(spec["scopes"], ["fs.read"])
        self.assertTrue(spec["side_effect"])
        self.assertTrue(spec["enabled"])
        self.assertTrue(spec["policy_classified"])


# ── 6. allowed_tools per-call block + allowed_scopes API roundtrip ─────────────

class EnforcementTest(unittest.TestCase):
    def test_allowed_tools_blocks_tool_per_call(self) -> None:
        tool = f"p92fix_at_{uuid.uuid4().hex[:8]}"
        reg.register_tool(name=tool, handler=lambda a: {"ok": True}, permission="auto", source="builtin")
        agent = f"p92fix-at-agent-{uuid.uuid4().hex[:6]}"
        # Restrict the agent to a different tool — the per-call preflight must block.
        mon.update_agent_limit(agent, {"allowed_tools": [f"other_{uuid.uuid4().hex[:6]}"]})
        calls: list[str] = []
        try:
            res = execute_tool(
                ToolExecutionRequest(
                    run_id="r", agent_id=agent, project_scope_id="",
                    tool_name=tool, args={}, source="test",
                ),
                dispatch_fn=lambda n, a: calls.append(n) or {"ok": True},
            )
            self.assertEqual(res.status, "blocked")
            self.assertEqual(calls, [], "tool outside allowed_tools must not dispatch")
        finally:
            reg.delete_tool(tool)

    def test_allowed_scopes_roundtrip_through_api(self) -> None:
        agent = f"p92fix-scope-{uuid.uuid4().hex[:6]}"
        r = client.put(f"/api/agent-os/limits/{agent}",
                       json={"allowed_scopes": ["fs.read", "net.outbound"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted(r.json()["allowed_scopes"]), ["fs.read", "net.outbound"])
        g = client.get(f"/api/agent-os/limits/{agent}")
        self.assertEqual(g.status_code, 200, g.text)
        self.assertEqual(sorted(g.json()["allowed_scopes"]), ["fs.read", "net.outbound"])


if __name__ == "__main__":
    unittest.main()
