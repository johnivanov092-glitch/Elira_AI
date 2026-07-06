"""Progress-aware controller + raw-SSH redirect + deterministic stop summary.

The loop-guards catch REPETITION. They do not catch a model issuing visibly-
different calls that all fail to move the world (the raw-ssh escaping spiral: 80
distinct commands, zero file changed, test still 7/8 — evaded near-dup for ~55
steps). This suite pins the closing behaviours:

  * raw `ssh host "…"` through run_bash is REDIRECTED to the ssh_* tools (not
    banned — an explicit marker still forces it);
  * a run of "doing" calls with no state change stops honestly (`no_progress`)
    with a DETERMINISTIC summary, not a model retelling;
  * calls that actually change files never trip it.
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import agent_loop  # noqa: E402
from app.application.code_agent.agent_loop import (  # noqa: E402
    _CODE_AGENT_BASE_TOOLS,
    _PROGRESS_STOP_AT,
)
from app.application.code_agent.tools._shell import raw_ssh_redirect  # noqa: E402
from app.application.code_agent.tools._run import tool_run_bash  # noqa: E402
from app.application.code_agent.loop_helpers import (  # noqa: E402
    _deterministic_stop_summary,
    _fact_shape,
    step_made_progress,
)
from app.application.agent_kernel import deferred_tools  # noqa: E402
from app.application.tool_providers import ToolRegistry  # noqa: E402


# ── raw-SSH redirect ────────────────────────────────────────────


class RawSshRedirectTest(unittest.TestCase):
    def test_raw_ssh_remote_exec_is_redirected(self) -> None:
        msg = raw_ssh_redirect('ssh root@home-srv01 "netstat -ano | findstr 18080"')
        self.assertIsNotNone(msg)
        self.assertIn("ssh_run", msg)
        self.assertIn("ssh_write", msg)
        self.assertIn("ssh_run_ps", msg)

    def test_explicit_override_marker_allows_raw_ssh(self) -> None:
        self.assertIsNone(raw_ssh_redirect('ssh host "curl x" #!raw-ssh'))

    def test_ssh_keygen_copy_scp_not_redirected(self) -> None:
        self.assertIsNone(raw_ssh_redirect("ssh-keygen -t ed25519 -f ~/.ssh/id"))
        self.assertIsNone(raw_ssh_redirect("ssh-copy-id user@host"))
        self.assertIsNone(raw_ssh_redirect("scp file host:/tmp/"))

    def test_local_command_not_redirected(self) -> None:
        self.assertIsNone(raw_ssh_redirect("python -c \"print(1)\""))
        self.assertIsNone(raw_ssh_redirect("git status"))

    def test_ssh_option_query_not_redirected(self) -> None:
        # `ssh -V` / `ssh -G host` are diagnostics, not the remote-exec trap.
        self.assertIsNone(raw_ssh_redirect("ssh -V"))

    def test_tool_run_bash_returns_redirect_with_ok_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = tool_run_bash(Path(tmp), command='ssh root@home-srv01 "type C:\\a.ps1"')
        self.assertFalse(res.get("ok", True))
        self.assertIn("ssh_run", res["text"])


# ── progress classification helpers ─────────────────────────────


class ProgressHelperTest(unittest.TestCase):
    def test_file_mutation_is_progress(self) -> None:
        self.assertTrue(step_made_progress(
            name="write_file", tool_meta={"text": "wrote", "touched_path": "f.py"},
            fact=None, seen_fact_shapes=set(),
        ))

    def test_action_tool_without_change_is_not_progress(self) -> None:
        self.assertFalse(step_made_progress(
            name="run_bash", tool_meta={"text": "$ ... exit=0"},
            fact="run_bash(x): exit=0", seen_fact_shapes=set(),
        ))

    def test_new_read_is_progress_repeat_is_not(self) -> None:
        seen: set[str] = set()
        fact = "read_file(a.py): def main(): ..."
        self.assertTrue(step_made_progress(
            name="read_file", tool_meta={"text": "..."}, fact=fact, seen_fact_shapes=seen,
        ))
        # Same knowledge again — not progress.
        self.assertFalse(step_made_progress(
            name="read_file", tool_meta={"text": "..."}, fact=fact, seen_fact_shapes=seen,
        ))

    def test_run_server_start_is_progress(self) -> None:
        self.assertTrue(step_made_progress(
            name="run_server", tool_meta={"text": "started pid 5", "ok": True},
            fact=None, seen_fact_shapes=set(),
        ))

    def test_fact_shape_collapses_changing_numbers(self) -> None:
        a = _fact_shape("run_bash(netstat): TCP 127.0.0.1:18080 LISTENING 4488")
        b = _fact_shape("run_bash(netstat): TCP 127.0.0.1:18080 LISTENING 7488")
        self.assertEqual(a, b)  # only the PID changed → same shape, not new knowledge

    def test_deterministic_summary_reports_no_files_and_facts(self) -> None:
        out = _deterministic_stop_summary(
            "нет прогресса", ["run_bash(a) ok", "run_bash(b) ok"], [], ["read_file(x): hi"],
        )
        self.assertIn("Файлы НЕ изменены", out)
        self.assertIn("2", out)  # call count
        self.assertIn("read_file(x)", out)  # facts carried
        self.assertIn("детерминированный", out)


# ── loop-level: no_progress stop ────────────────────────────────

_FAKE_SCHEMAS = [
    {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
    for n in _CODE_AGENT_BASE_TOOLS
]


@contextlib.contextmanager
def _loop_env():
    with patch.object(agent_loop, "_resolve_code_route", return_value=("test-model", 32768, None)), \
         patch.object(agent_loop, "_record_code_route_metric"), \
         patch.object(agent_loop, "build_mcp_providers", return_value=[]), \
         patch.object(ToolRegistry, "collect_schemas", return_value=list(_FAKE_SCHEMAS)), \
         patch("app.application.agent_registry.sandbox.preflight_or_raise",
               return_value={"limit": {"max_execution_seconds": 600}}):
        yield


class _SeqChat:
    def __init__(self, responses, fallback):
        self._r = responses
        self._fb = fallback
        self._i = 0

    def __call__(self, **kw):
        r = self._r[self._i] if self._i < len(self._r) else self._fb
        self._i += 1
        return r


def _call(name, **args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def _final(text="готово"):
    return {"message": {"content": text, "tool_calls": []}}


# 12 clearly-distinct shell commands (low token overlap → NOT near-dup, distinct
# fingerprints → NOT the exact-repeat guard) so the ONLY thing that can stop them
# is the progress controller.
_DISTINCT_CMDS = [
    "echo alpha", "ls /var", "pwd", "date -u", "whoami", "hostname",
    "uptime", "cat /etc/os", "stat /bin", "wc /tmp/x", "tail /log/y", "head /log/z",
]


class NoProgressLoopTest(unittest.TestCase):
    def tearDown(self):
        for rid in ("np-stuck", "np-progress"):
            deferred_tools.clear_run(rid)

    def test_distinct_but_stateless_calls_stop_as_no_progress(self):
        calls = [_call("run_bash", command=c) for c in _DISTINCT_CMDS]
        chat = _SeqChat(calls, calls[-1])
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec",
                          return_value=SimpleNamespace(status="ok", output={"text": "out", "ok": True})):
            evs = list(agent_loop.stream_code_agent(
                user_message="почини сервис", project_root=tmp, run_id="np-stuck",
                auto_remember=False, permission_mode="bypass", max_steps=40, chat_fn=chat,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "no_progress")
        self.assertIn("no verified progress", str(done.get("error")))
        self.assertLessEqual(done["steps"], _PROGRESS_STOP_AT + 1)
        # Deterministic (not model-authored) closing summary.
        final = [e for e in evs if e.get("type") == "final_response"][-1]
        self.assertIn("Файлы НЕ изменены", final["text"])

    def test_calls_that_change_files_never_trip(self):
        # Every call reports a touched_path → real progress → streak resets, so a
        # long run of distinct file-mutating calls finishes with a clean answer.
        calls = [_call("run_bash", command=c) for c in _DISTINCT_CMDS]
        chat = _SeqChat(calls + [_final("сделано")], _final())
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec",
                          return_value=SimpleNamespace(
                              status="ok", output={"text": "out", "ok": True, "touched_path": "f.py"})):
            evs = list(agent_loop.stream_code_agent(
                user_message="почини сервис", project_root=tmp, run_id="np-progress",
                auto_remember=False, permission_mode="bypass", max_steps=40, chat_fn=chat,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "answer")


if __name__ == "__main__":
    unittest.main()
