"""ssh_request_host: the agent can't open the SSH allowlist itself, so it asks
the user to approve ONE host with a button. On approve the loop adds the host
(ssh_run then works); on deny/timeout it is NOT added. This keeps the security
boundary (user decides) while letting the agent drive the whole flow."""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app.application.code_agent import agent_loop
from app.application.code_agent.agent_loop import stream_code_agent, submit_answer
from app.application.tool_providers import ssh_acl


def _ssh_req_chat(host="elira-ai-server", reason="ключ установлен"):
    state = {"asked": False}

    def chat_fn(**kw):
        if not kw.get("tools"):
            return {"message": {"content": "summary", "tool_calls": []}}
        if not state["asked"]:
            state["asked"] = True
            return {"message": {"content": "", "tool_calls": [{
                "function": {"name": "ssh_request_host", "arguments": {"host": host, "reason": reason}},
            }]}}
        last_tool = next(
            (m for m in reversed(kw.get("messages") or []) if m.get("role") == "tool"), {})
        return {"message": {"content": f"OK. {last_tool.get('content','')}", "tool_calls": []}}

    return chat_fn


def _answer_pending(value, deadline_s=25):
    """From another thread: answer the first pending question with `value`."""
    def worker():
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            with agent_loop._QUESTION_LOCK:
                qids = [q for q, a in agent_loop._QUESTION_ANSWERS.items() if a is None]
            if qids:
                submit_answer(qids[0], value)
                return
            time.sleep(0.02)
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t


class SshRequestHostTest(unittest.TestCase):
    def _run(self, chat_fn, run_id, acl_path, no_questions=False):
        with mock.patch.object(ssh_acl, "ACL_PATH", acl_path):
            with tempfile.TemporaryDirectory() as tmp:
                return list(stream_code_agent(
                    user_message="настрой ssh и добавь хост", project_root=tmp,
                    model="test-model", max_steps=6, chat_fn=chat_fn, run_id=run_id,
                    approval_wait_seconds=30, auto_remember=False, no_questions=no_questions,
                ))

    def test_approve_adds_host_to_allowlist(self):
        with tempfile.TemporaryDirectory() as d:
            acl = Path(d) / "ssh_acl.json"  # starts absent = empty allowlist
            t = _answer_pending("Одобрить")
            evs = self._run(_ssh_req_chat("elira-ai-server"), "ssh-ok", acl)
            t.join(timeout=2)
            # a question card with the two buttons was shown
            pend = [e for e in evs if e.get("type") == "question_pending"]
            self.assertEqual(len(pend), 1)
            self.assertEqual(pend[0]["options"], ["Одобрить", "Отклонить"])
            # the host really landed in the (isolated) allowlist
            with mock.patch.object(ssh_acl, "ACL_PATH", acl):
                self.assertIn("elira-ai-server", ssh_acl.get_allowed_hosts())
            finals = [e for e in evs if e.get("type") == "final_response"]
            self.assertIn("добавлен", finals[-1]["text"])

    def test_deny_does_not_add_host(self):
        with tempfile.TemporaryDirectory() as d:
            acl = Path(d) / "ssh_acl.json"
            t = _answer_pending("Отклонить")
            evs = self._run(_ssh_req_chat("evil-host"), "ssh-deny", acl)
            t.join(timeout=2)
            with mock.patch.object(ssh_acl, "ACL_PATH", acl):
                self.assertNotIn("evil-host", ssh_acl.get_allowed_hosts())
            finals = [e for e in evs if e.get("type") == "final_response"]
            self.assertIn("ОТКЛОНИЛ", finals[-1]["text"])

    def test_already_present_skips_the_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            acl = Path(d) / "ssh_acl.json"
            with mock.patch.object(ssh_acl, "ACL_PATH", acl):
                ssh_acl.set_allowed_hosts(["elira-ai-server"])
            evs = self._run(_ssh_req_chat("elira-ai-server"), "ssh-already", acl)
            # no human pause when the host is already allowed
            self.assertEqual([e for e in evs if e.get("type") == "question_pending"], [])
            finals = [e for e in evs if e.get("type") == "final_response"]
            self.assertIn("уже", finals[-1]["text"])

    def test_pauses_even_with_no_questions(self):
        # Unlike ask_user, opening the SSH allowlist is a security decision — it
        # must ALWAYS wait for the human, even when «не спрашивать» is on.
        with tempfile.TemporaryDirectory() as d:
            acl = Path(d) / "ssh_acl.json"
            t = _answer_pending("Одобрить")
            evs = self._run(_ssh_req_chat("elira-ai-server"), "ssh-noq", acl, no_questions=True)
            t.join(timeout=2)
            self.assertEqual(len([e for e in evs if e.get("type") == "question_pending"]), 1)
            with mock.patch.object(ssh_acl, "ACL_PATH", acl):
                self.assertIn("elira-ai-server", ssh_acl.get_allowed_hosts())


if __name__ == "__main__":
    unittest.main()
