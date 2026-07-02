from __future__ import annotations

import os
import unittest
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


class IsCriticalFailClosedTest(unittest.TestCase):
    """FIX-7: if the shell-criticality check errors, treat the command as critical."""

    def test_fail_closed_when_check_raises(self):
        from app.application.code_agent import loop_helpers
        with patch("app.application.code_agent.tools.is_shell_critical",
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


if __name__ == "__main__":
    unittest.main()
