from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class AgentChildEnvTest(unittest.TestCase):
    """FIX-1: agent-spawned shell/server children must not inherit secret env."""

    def test_secret_keys_stripped_toolchain_kept(self):
        from app.application.code_agent.tools._run import _agent_child_env
        fake = {
            "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x",
            "HUGGINGFACE_TOKEN": "hf_y",
            "ELIRA_API_TOKEN": "z",
            "LLAMA_SERVER_API_KEY": "k",
            "MY_APP_SECRET": "s",
            "SOME_PASSWORD": "p",
            "PATH": "/usr/bin",
            "NORMAL_VAR": "1",
        }
        with patch.dict(os.environ, fake, clear=True):
            env = _agent_child_env()
        for leaked in ("GITHUB_PERSONAL_ACCESS_TOKEN", "HUGGINGFACE_TOKEN",
                       "ELIRA_API_TOKEN", "LLAMA_SERVER_API_KEY",
                       "MY_APP_SECRET", "SOME_PASSWORD"):
            self.assertNotIn(leaked, env)
        self.assertEqual(env.get("PATH"), "/usr/bin")   # toolchain preserved
        self.assertEqual(env.get("NORMAL_VAR"), "1")


class SandboxEnvLeakTest(unittest.TestCase):
    """FIX-1 (completion): sandbox_run — arbitrary model Python — must NOT get
    Elira's secrets in its env. This is the most dangerous inheritance path."""

    def test_sandbox_child_env_has_no_secrets(self):
        from app.application.code_agent import sandbox as sb
        captured: dict = {}

        def fake_run(cmd, **kw):
            captured["env"] = kw.get("env")
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        with tempfile.TemporaryDirectory() as tmp:
            box = Path(tmp) / "box"
            (box / "work").mkdir(parents=True)
            with patch.dict(os.environ, {
                "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_LEAK",
                "PATH": os.environ.get("PATH", "/usr/bin"),
            }, clear=False), \
                    patch.object(sb, "_ensure_sandbox", return_value=box), \
                    patch.object(sb, "_venv_python", return_value=box / "python"), \
                    patch.object(sb.subprocess, "run", side_effect=fake_run):
                sb.run_in_sandbox(box, code="print(1)", timeout=5)

        env = captured["env"]
        self.assertIsNotNone(env)
        self.assertNotIn("GITHUB_PERSONAL_ACCESS_TOKEN", env)  # secret stripped
        self.assertIn("PATH", env)                             # toolchain kept
        self.assertEqual(env.get("PYTHONUTF8"), "1")           # utf-8 knob layered


class IsCriticalFailClosedTest(unittest.TestCase):
    """FIX-7: if the shell-criticality check errors, treat the command as critical."""

    def test_fail_closed_when_check_raises(self):
        from app.application.code_agent import loop_helpers
        with patch.object(loop_helpers, "evidence_for_tool_call",
                          side_effect=RuntimeError("boom")):
            self.assertTrue(loop_helpers._is_critical_call("run_bash", {"command": "echo hi"}))

    def test_normal_path_still_classifies(self):
        from app.application.code_agent import loop_helpers
        self.assertTrue(loop_helpers._is_critical_call("run_bash", {"command": "rm -rf /data"}))
        self.assertFalse(loop_helpers._is_critical_call("run_bash", {"command": "ls -la"}))


class PinnedPatchTest(unittest.TestCase):
    """FIX-2: pinned is written only when the client explicitly sends it."""

    def test_pinned_not_clobbered_on_unrelated_patch(self):
        from app.api.routes import code_agent_routes as R
        captured: dict = {}

        def fake_update(session_id, patch_data):
            captured.clear()
            captured.update(patch_data)
            return {"id": session_id}

        with patch.object(R.session_store, "update_session", side_effect=fake_update):
            R.patch_code_session("s1", R.SessionPatchRequest(turns=[]))
            self.assertNotIn("pinned", captured)          # not sent → not written
            R.patch_code_session("s1", R.SessionPatchRequest(pinned=True))
            self.assertEqual(captured.get("pinned"), True)  # explicit → written


class DataDirRedirectTest(unittest.TestCase):
    """FIX-6: config paths honour ELIRA_DATA_DIR so pytest never writes live data/."""

    def test_config_paths_follow_env_not_live_tree(self):
        from app.core import config
        expected = Path(os.environ["ELIRA_DATA_DIR"]).resolve()
        self.assertEqual(config.DATA_DIR, expected)
        self.assertEqual(config.UPLOAD_DIR, expected / "uploads")
        self.assertEqual(config.GENERATED_DIR, expected / "generated")
        # and it is NOT the developer's live <repo>/data tree
        self.assertNotEqual(config.DATA_DIR, (config.ROOT_DIR / "data").resolve())


if __name__ == "__main__":
    unittest.main()
