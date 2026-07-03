from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.project_prompt import get_verify_command


class GetVerifyCommandTest(unittest.TestCase):
    def test_reads_first_meaningful_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".elira").mkdir()
            (root / ".elira" / "verify").write_text("# comment\n\npytest -q\nignored", encoding="utf-8")
            self.assertEqual(get_verify_command(root), "pytest -q")

    def test_absent_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(get_verify_command(tmp))

    def test_reads_bom_encodings(self):
        # Files created on Windows carry a BOM (PowerShell / Notepad); the reader
        # must still recover the command, not return "﻿pytest -q" or None.
        for data in (
            "pytest -q\n".encode("utf-8-sig"),   # UTF-8 with BOM
            "pytest -q\n".encode("utf-16"),       # UTF-16 with BOM (PS 5.1 default)
        ):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".elira").mkdir()
                (root / ".elira" / "verify").write_bytes(data)
                self.assertEqual(get_verify_command(root), "pytest -q")


def _edit_then_finish_chat():
    """chat_fn: first tool-enabled call writes a file, then always finalizes."""
    state = {"edited": False}

    def chat_fn(**kw):
        if not kw.get("tools"):
            return {"message": {"content": "summary", "tool_calls": []}}
        if not state["edited"]:
            state["edited"] = True
            return {"message": {"content": "", "tool_calls": [{
                "function": {"name": "write_file", "arguments": {"path": "a.txt", "content": "y"}},
            }]}}
        return {"message": {"content": "Готово.", "tool_calls": []}}

    return chat_fn


def _drive(verify_content, run_bash_text, run_id):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".elira").mkdir()
        (root / ".elira" / "verify").write_text(verify_content, encoding="utf-8")
        with patch("app.application.code_agent.tools.tool_run_bash",
                   return_value={"text": run_bash_text}):
            return list(stream_code_agent(
                user_message="fix", project_root=str(root), model="test-model",
                max_steps=10, chat_fn=_edit_then_finish_chat(), run_id=run_id,
                approval_wait_seconds=0, auto_remember=False,
                permission_mode="bypass",  # write_file auto-approves, no pause
            ))


def _verify_calls(events):
    return [e for e in events if e.get("type") == "tool_call"
            and e.get("tool") == "run_bash"
            and e.get("arguments", {}).get("command") == "pytest -q"]


class VerifyGateLoopTest(unittest.TestCase):
    def test_green_verify_allows_finalize(self):
        evs = _drive("pytest -q", "$ pytest -q\nexit=0\nSTDOUT:\nok", "vg-green")
        vc = _verify_calls(evs)
        self.assertEqual(len(vc), 1)          # ran once
        self.assertTrue(vc[0]["ok"])          # passed
        finals = [e for e in evs if e.get("type") == "final_response"]
        self.assertIn("Готово", finals[-1]["text"])   # finalized on the answer

    def test_red_verify_is_bounded_then_finalizes(self):
        evs = _drive("pytest -q", "$ pytest -q\nexit=1\nSTDERR:\nboom", "vg-red")
        vc = _verify_calls(evs)
        self.assertEqual(len(vc), 3)          # capped at _VERIFY_GATE_MAX
        self.assertFalse(any(v["ok"] for v in vc))
        self.assertTrue([e for e in evs if e.get("type") == "final_response"])  # still finalizes


class NoVerifyFileTest(unittest.TestCase):
    def test_no_verify_file_means_no_gate(self):
        with tempfile.TemporaryDirectory() as tmp:  # no .elira/verify
            evs = list(stream_code_agent(
                user_message="fix", project_root=tmp, model="test-model",
                max_steps=10, chat_fn=_edit_then_finish_chat(), run_id="vg-none",
                approval_wait_seconds=0, auto_remember=False, permission_mode="bypass",
            ))
        self.assertEqual(_verify_calls(evs), [])  # gate never runs
        self.assertTrue([e for e in evs if e.get("type") == "final_response"])


if __name__ == "__main__":
    unittest.main()
