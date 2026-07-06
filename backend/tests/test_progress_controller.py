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
from app.application.code_agent.agent_loop import _CODE_AGENT_BASE_TOOLS  # noqa: E402
from app.application.code_agent.tools._shell import raw_ssh_redirect  # noqa: E402
from app.application.code_agent.tools._run import tool_run_bash  # noqa: E402
from app.application.code_agent.loop_helpers import _deterministic_stop_summary  # noqa: E402
from app.application.code_agent.progress import (  # noqa: E402
    CONSECUTIVE_NO_PROGRESS_CAP,
    ProgressEvaluator,
    _fact_shape,
    step_made_progress,
    strategy_family,
    strategy_target,
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

    def test_raw_ssh_with_options_is_redirected(self) -> None:
        # The live-run format the old regex missed: options before the host.
        self.assertIsNotNone(raw_ssh_redirect('ssh -o BatchMode=yes home-srv01 "netstat -ano"'))
        self.assertIsNotNone(raw_ssh_redirect('ssh -i ~/.ssh/id -p 2222 host "type C:\\a.ps1"'))
        self.assertIsNotNone(raw_ssh_redirect("ssh -oStrictHostKeyChecking=no host cmd"))  # glued opt

    def test_ssh_diagnostic_and_interactive_not_redirected(self) -> None:
        self.assertIsNone(raw_ssh_redirect("ssh -V"))
        self.assertIsNone(raw_ssh_redirect("ssh -G host"))
        self.assertIsNone(raw_ssh_redirect("ssh home-srv01"))  # interactive, no remote command

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

    def test_remote_command_fresh_fact_is_progress_repeat_is_not(self) -> None:
        seen: set[str] = set()
        fact = (
            "ssh_run(home-srv01): exit=0 STDOUT: "
            "AgentLab service started PID 7684 WARNING: Port 18080 not found"
        )
        self.assertTrue(step_made_progress(
            name="ssh_run", tool_meta={"text": "exit=0"}, fact=fact, seen_fact_shapes=seen,
        ))
        self.assertFalse(step_made_progress(
            name="ssh_run", tool_meta={"text": "exit=0"}, fact=fact, seen_fact_shapes=seen,
        ))

        ps_fact = "ssh_run_ps(home-srv01): exit=0 STDOUT: start.ps1 header"
        self.assertTrue(step_made_progress(
            name="ssh_run_ps", tool_meta={"text": "exit=0"}, fact=ps_fact, seen_fact_shapes=seen,
        ))

    def test_fact_shape_collapses_changing_numbers(self) -> None:
        a = _fact_shape("run_bash(netstat): TCP 127.0.0.1:18080 LISTENING 4488")
        b = _fact_shape("run_bash(netstat): TCP 127.0.0.1:18080 LISTENING 7488")
        self.assertEqual(a, b)  # only the PID changed → same shape, not new knowledge

    def test_deterministic_summary_is_a_structured_incomplete_report(self) -> None:
        out = _deterministic_stop_summary(
            "нет прогресса", ["run_bash(a) ok", "run_bash(b) ok"], [], ["read_file(x): hi"],
            exhausted_strategies=["remote_ps@h", "remote_edit:ssh_write@h:/f"],
            next_step="ssh_replace / ssh_write",
        )
        self.assertIn("Не завершено", out)
        self.assertIn("Файлы не изменены", out)
        self.assertIn("2", out)                       # call count
        self.assertIn("read_file(x)", out)            # facts carried
        self.assertIn("remote_ps@h", out)             # exhausted strategies named
        self.assertIn("Следующий безопасный шаг", out)
        self.assertIn("ssh_replace / ssh_write", out)  # concrete next step
        self.assertIn("Детерминированный", out)


# ── strategy router (the core) ──────────────────────────────────


class StrategyRouterTest(unittest.TestCase):
    def test_strategy_family_and_target_derivation(self) -> None:
        self.assertEqual(strategy_family("ssh_write", {"host": "h", "path": "/f"}), "remote_edit:ssh_write")
        self.assertEqual(strategy_family("ssh_replace", {"host": "h", "path": "/f"}), "remote_edit:ssh_replace")
        self.assertEqual(strategy_family("ssh_run_ps", {"host": "h", "script": "x"}), "remote_ps")
        self.assertEqual(strategy_family("write_file", {"path": "a.py"}), "local_edit")
        # raw ssh with an edit marker → the inline-powershell quoting family
        self.assertEqual(
            strategy_family("run_bash", {"command": 'ssh h "Set-Content C:\\a.ps1"'}),
            "remote_edit:inline_ps",
        )
        self.assertEqual(strategy_target("ssh_write", {"host": "h", "path": "/f"}), "h:/f")

    def test_same_method_exhausts_in_two_then_redirects(self) -> None:
        ev = ProgressEvaluator()
        a = {"host": "h", "path": "/f", "content": "x"}
        v1 = ev.evaluate(name="ssh_write", args=a, tool_meta={"text": "ERROR: remote write failed"}, fact=None)
        self.assertFalse(v1.exhausted)
        self.assertIsNone(v1.redirect)
        v2 = ev.evaluate(name="ssh_write", args=a, tool_meta={"text": "ERROR: remote write failed"}, fact=None)
        self.assertTrue(v2.exhausted)
        self.assertIsNotNone(v2.redirect)      # redirect, NOT stop
        self.assertFalse(v2.should_stop)

    def test_two_exhausted_families_same_target_stops(self) -> None:
        ev = ProgressEvaluator()
        t = {"host": "h", "path": "/f"}
        for _ in range(2):
            ev.evaluate(name="ssh_write", args={**t, "content": "x"}, tool_meta={"text": "ERROR"}, fact=None)
        # second family on the SAME target — exhausting it stops the run honestly.
        ev.evaluate(name="ssh_replace", args={**t, "old": "a", "new": "b"}, tool_meta={"text": "ERROR"}, fact=None)
        v = ev.evaluate(name="ssh_replace", args={**t, "old": "c", "new": "d"}, tool_meta={"text": "ERROR"}, fact=None)
        self.assertTrue(v.should_stop)
        self.assertIn("remote_edit:ssh_write@h:/f", ev.exhausted_summary())
        self.assertIn("remote_edit:ssh_replace@h:/f", ev.exhausted_summary())

    def test_progress_resets_the_strategy(self) -> None:
        ev = ProgressEvaluator()
        a = {"host": "h", "path": "/f", "content": "x"}
        ev.evaluate(name="ssh_write", args=a, tool_meta={"text": "ERROR"}, fact=None)
        ev.evaluate(name="ssh_write", args=a, tool_meta={"text": "ERROR"}, fact=None)  # exhausted
        # a real change → the method is re-armed, exhaustion cleared.
        v = ev.evaluate(name="ssh_write", args=a, tool_meta={"text": "ok", "touched_path": "/f"}, fact=None)
        self.assertEqual(v.status, "progress")
        self.assertEqual(ev.exhausted_summary(), [])

    def test_remote_verify_facts_do_not_exhaust_host_strategies(self) -> None:
        ev = ProgressEvaluator()
        write = ev.evaluate(
            name="ssh_write",
            args={"host": "home-srv01", "path": "C:/AgentLab/start.ps1", "content": "x"},
            tool_meta={"text": "Wrote", "touched_path": "ssh:home-srv01:C:/AgentLab/start.ps1"},
            fact="ssh_write(C:/AgentLab/start.ps1): Wrote",
        )
        self.assertEqual(write.status, "progress")

        ps_check = ev.evaluate(
            name="ssh_run_ps",
            args={"host": "home-srv01", "script": "Get-Content C:\\AgentLab\\start.ps1"},
            tool_meta={"text": "exit=0\nSTDOUT: header"},
            fact="ssh_run_ps(home-srv01): exit=0 STDOUT: header",
        )
        self.assertEqual(ps_check.status, "progress")
        self.assertFalse(ps_check.should_stop)

        start = ev.evaluate(
            name="ssh_run",
            args={
                "host": "home-srv01",
                "command": (
                    "powershell.exe -NoProfile -ExecutionPolicy Bypass "
                    "-File \"C:\\AgentLab\\start.ps1\""
                ),
            },
            tool_meta={
                "text": (
                    "exit=0\nSTDOUT: AgentLab service started PID 7684\n"
                    "WARNING: Port 18080 not found"
                )
            },
            fact=(
                "ssh_run(home-srv01): exit=0 STDOUT: AgentLab service started "
                "PID 7684 WARNING: Port 18080 not found"
            ),
        )
        self.assertEqual(start.status, "progress")
        self.assertFalse(start.should_stop)
        self.assertEqual(ev.consecutive_no_progress, 0)
        self.assertEqual(ev.exhausted_summary(), [])

    def test_tool_host_budget_stops_a_stuck_host(self) -> None:
        # A host that eats 5 failing ssh_run calls (one family) stops via the
        # per-(tool,host) budget even though only ONE family is involved.
        ev = ProgressEvaluator()
        v = None
        for i in range(5):
            v = ev.evaluate(
                name="ssh_run", args={"host": "h", "command": f"netstat {i}"},
                tool_meta={"text": "exit=1"}, fact=None,
            )
        self.assertTrue(v.should_stop)
        self.assertIn("h", v.stop_detail)

    def test_next_step_hint_names_a_concrete_alternative(self) -> None:
        ev = ProgressEvaluator()
        a = {"host": "h", "path": "/f", "content": "x"}
        ev.evaluate(name="ssh_write", args=a, tool_meta={"text": "ERROR"}, fact=None)
        ev.evaluate(name="ssh_write", args=a, tool_meta={"text": "ERROR"}, fact=None)  # exhausted
        self.assertIn("ssh_replace", ev.next_step_hint())

    def test_consecutive_not_cumulative_no_progress_stop(self) -> None:
        # A productive run: many no-progress calls BROKEN UP by real progress must
        # never hit the backstop — the cap is on CONSECUTIVE no-progress, not total.
        ev = ProgressEvaluator()
        for _ in range(3):
            for i in range(CONSECUTIVE_NO_PROGRESS_CAP - 1):
                v = ev.evaluate(name="run_bash", args={"command": f"probe {i}"},
                                tool_meta={"text": "x"}, fact=None)
                self.assertFalse(v.should_stop)
            # a real change resets the consecutive streak
            p = ev.evaluate(name="write_file", args={"path": "f"},
                            tool_meta={"text": "ok", "touched_path": "f"}, fact=None)
            self.assertEqual(p.status, "progress")
        self.assertGreater(ev.no_progress_total, CONSECUTIVE_NO_PROGRESS_CAP)  # cumulative is high…
        self.assertEqual(ev.consecutive_no_progress, 0)                         # …but consecutive reset

    def test_consecutive_streak_stops_when_uninterrupted(self) -> None:
        ev = ProgressEvaluator()
        v = None
        for i in range(CONSECUTIVE_NO_PROGRESS_CAP):
            v = ev.evaluate(name="run_bash", args={"command": f"probe {i}"},
                            tool_meta={"text": "x"}, fact=None)
        self.assertTrue(v.should_stop)
        self.assertIn("ПОДРЯД", v.stop_detail)


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
        self.assertLessEqual(done["steps"], CONSECUTIVE_NO_PROGRESS_CAP + 1)
        # Deterministic (not model-authored) closing report.
        final = [e for e in evs if e.get("type") == "final_response"][-1]
        self.assertIn("Не завершено", final["text"])
        self.assertIn("Файлы не изменены", final["text"])

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


# ── coding strategy families (Ph7.1) ────────────────────────────


class CodingFamiliesTest(unittest.TestCase):
    def _fam(self, cmd):
        return strategy_family("run_bash", {"command": cmd})

    def test_family_classification(self):
        self.assertEqual(self._fam("pytest tests/test_x.py::test_y"), "test:focused")
        self.assertEqual(self._fam("pytest -k parser"), "test:focused")
        self.assertEqual(self._fam("pytest"), "test:full")
        self.assertEqual(self._fam("python -m pytest tests/"), "test:full")
        self.assertEqual(self._fam("npm --prefix frontend run typecheck"), "verify:typecheck")
        self.assertEqual(self._fam("npm --prefix frontend run build"), "verify:build")
        self.assertEqual(self._fam("bash .elira/verify"), "verify:project")
        self.assertEqual(self._fam('python -c "import app.main"'), "verify:import")
        self.assertEqual(self._fam("npm run dev"), "run:app")

    def test_run_bash_is_not_investigation(self):
        # a coding test's changing stdout must NOT read as progress
        from app.application.code_agent.progress import INVESTIGATION_TOOLS
        self.assertNotIn("run_bash", INVESTIGATION_TOOLS)

    def test_repeated_failing_test_redirects_not_loops(self):
        ev = ProgressEvaluator()
        a = {"command": "pytest tests/test_x.py"}
        v1 = ev.evaluate(name="run_bash", args=a, tool_meta={"text": "F", "exit_code": 1}, fact=None)
        self.assertIsNone(v1.redirect)
        v2 = ev.evaluate(name="run_bash", args=a, tool_meta={"text": "F", "exit_code": 1}, fact=None)
        self.assertTrue(v2.exhausted)
        self.assertIsNotNone(v2.redirect)
        self.assertIn("трейсбек", v2.redirect)  # coding-specific redirect

    def test_green_test_after_edit_is_progress_not_exhaustion(self):
        # edit → test-fail → edit → test-PASS must read the pass as progress, not
        # exhaust the test strategy and fire a spurious redirect.
        ev = ProgressEvaluator()
        edit = {"path": "x.py"}
        test = {"command": "pytest tests/test_x.py"}
        ev.evaluate(name="write_file", args=edit, tool_meta={"text": "ok", "touched_path": "x.py"}, fact=None)
        ev.evaluate(name="run_bash", args=test, tool_meta={"text": "F", "exit_code": 1}, fact=None)
        ev.evaluate(name="write_file", args=edit, tool_meta={"text": "ok", "touched_path": "x.py"}, fact=None)
        v = ev.evaluate(name="run_bash", args=test, tool_meta={"text": "8 passed", "exit_code": 0}, fact=None)
        self.assertEqual(v.status, "progress")
        self.assertFalse(v.should_stop)

    def test_repeated_green_test_is_not_new_progress(self):
        ev = ProgressEvaluator()
        test = {"command": "pytest tests/test_x.py"}
        v1 = ev.evaluate(name="run_bash", args=test, tool_meta={"text": "ok", "exit_code": 0}, fact=None)
        self.assertEqual(v1.status, "progress")            # first green
        v2 = ev.evaluate(name="run_bash", args=test, tool_meta={"text": "ok", "exit_code": 0}, fact=None)
        self.assertEqual(v2.status, "no_progress")         # re-running green ≠ progress


if __name__ == "__main__":
    unittest.main()
