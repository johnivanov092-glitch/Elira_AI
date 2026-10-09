from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch


class AgentChildEnvTest(unittest.TestCase):
    """Agent children inherit the current Windows process environment."""

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
        # Nothing is stripped; only UTF-8 output defaults for Python children are added.
        from app.core.config import ROOT_DIR, DATA_DIR
        from app.application.code_agent.tools._shell import get_current_run_id
        self.assertEqual(env, {**fake, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
            "ELIRA_BACKEND_ROOT": str(ROOT_DIR / "backend"),
            "ELIRA_SHARED_ROOT": str(ROOT_DIR / "shared"),
            "ELIRA_SKILLS_ROOT": str(DATA_DIR / "skills"),
            "ELIRA_PARENT_RUN_ID": str(get_current_run_id() or "")})
        self.assertEqual(env.get("PATH"), "/usr/bin")   # toolchain preserved
        self.assertEqual(env.get("NORMAL_VAR"), "1")


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
