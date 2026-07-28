"""Grounding-across-turns regression lock.

Root cause found in live QA: conversation_history drops tool results, so a
factual follow-up ("какие файлы?") gets confabulated ("calc.py/multiply") when
the grounded facts aren't in context. Fix = carry a compact "established facts"
digest into the next turn's history as an authoritative system block, plus a
prompt rule to re-verify via tools. These tests pin the PLUMBING (the model's
own re-grounding is a live eval, not a unit test):
  - discovery tools produce fact lines; actions/failures/empties do not;
  - the digest dedups + caps;
  - _coerce_history re-tags the facts block to a system message;
  - a real stream run emits `established_facts` after a grounding tool call.
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import agent_loop  # noqa: E402
from app.application.code_agent.agent_loop import _CODE_AGENT_BASE_TOOLS  # noqa: E402
from app.application.code_agent.loop_helpers import (  # noqa: E402
    FACTS_PREFIX,
    _answer_admits_missing_external_evidence,
    _fact_from_tool,
    _facts_digest,
    _requires_external_evidence,
    _tool_provides_external_evidence,
    _ungrounded_files,
)
from app.application.code_agent.history import _coerce_history  # noqa: E402
from app.application.agent_kernel import deferred_tools  # noqa: E402
from app.application.tool_providers import ToolRegistry  # noqa: E402


class FactHelperTest(unittest.TestCase):
    def test_grounding_tool_yields_fact(self):
        f = _fact_from_tool("read_file", "main.py", "def add(a, b): return a + b")
        self.assertIsNotNone(f)
        self.assertIn("read_file", f)
        self.assertIn("main.py", f)
        self.assertIn("def add", f)

    def test_non_grounding_tool_none(self):
        self.assertIsNone(_fact_from_tool("todo_update", "x", "ok"))
        self.assertIsNone(_fact_from_tool("ask_user", "?", "answer"))

    def test_failed_or_empty_yields_none(self):
        self.assertIsNone(_fact_from_tool("read_file", "x", "content", ok=False))
        self.assertIsNone(_fact_from_tool("glob", "*.py", "   \n  "))

    def test_read_snippet_is_truncated(self):
        f = _fact_from_tool("run_bash", "ls", "x" * 5000)
        self.assertLess(len(f), 500)  # non-enum tools stay compact (cap 400)

    def test_enumeration_tools_carry_full_listing(self):
        # project_map / glob reveal the COMPLETE file set — carry it in full so the
        # model stops inventing extra files on top of a truncated list.
        long_listing = " ".join(f"file{i}.py" for i in range(200))
        g = _fact_from_tool("glob", "**/*.py", long_listing)
        r = _fact_from_tool("read_file", "x.py", long_listing)
        self.assertGreater(len(g), 500)          # enum carries a big snippet
        self.assertLess(len(r), 500)             # a normal read stays compact
        self.assertGreater(len(g), len(r) * 2)

    def test_digest_dedups_and_caps(self):
        d = _facts_digest(["read_file(a): x", "read_file(a): x", "glob: b"])
        self.assertEqual(d.count("read_file(a): x"), 1)
        self.assertIn("glob: b", d)
        big = _facts_digest([f"read_file(f{i}): " + "z" * 300 for i in range(80)])
        self.assertLessEqual(len(big), 6010)

    def test_empty_digest(self):
        self.assertEqual(_facts_digest([]), "")


class ExternalEvidenceHelperTest(unittest.TestCase):
    def test_real_company_creator_requires_external_evidence(self):
        self.assertTrue(_requires_external_evidence(
            "Кто сделал модель Laguna и из какой она компании?"
        ))

    def test_high_stakes_advice_requires_external_evidence(self):
        self.assertTrue(_requires_external_evidence(
            "Можно ли при беременности и низкой плаценте заниматься сексом?"
        ))

    def test_ordinary_code_question_does_not_require_web(self):
        self.assertFalse(_requires_external_evidence(
            "Объясни, почему эта функция возвращает None."
        ))

    def test_local_project_checks_do_not_require_web(self):
        for question in (
            "Проверь последний проект и запусти тесты.",
            "Найди файл config.py и прочитай его.",
            "Посмотри текущую нагрузку на моём сервере через SSH.",
            "Кто сделал этот коммит в проекте?",
        ):
            with self.subTest(question=question):
                self.assertFalse(_requires_external_evidence(question))

    def test_claimed_official_check_requires_evidence(self):
        self.assertTrue(_requires_external_evidence(
            "Расскажи об этом районе.",
            "Я проверил официальный реестр и сайт акимата.",
        ))

    def test_honest_uncertainty_can_finalize(self):
        self.assertTrue(_answer_admits_missing_external_evidence(
            "Источник не найден, поэтому факт не подтвержден."
        ))

    def test_search_snippet_is_not_content_evidence(self):
        self.assertFalse(_tool_provides_external_evidence(
            "web_search", "1. Example https://example.com"
        ))
        self.assertTrue(_tool_provides_external_evidence(
            "web_fetch", "[web_fetch: https://example.com] full page"
        ))


class CoerceHistoryFactsTest(unittest.TestCase):
    def test_facts_block_retagged_to_system(self):
        out = _coerce_history([
            {"role": "user", "content": "какие файлы?"},
            {"role": "assistant", "content": "Файлы: main.py, utils.py"},
            {"role": "assistant", "content": f"{FACTS_PREFIX}\n- glob: main.py utils.py data.json"},
        ])
        systems = [m for m in out if m["role"] == "system"]
        self.assertTrue(systems, "facts block must become a system message")
        self.assertIn("main.py", systems[-1]["content"])
        # it must NOT survive as an assistant 'my own prior reply'
        self.assertFalse(
            any(m["role"] == "assistant" and "ПРОВЕРЕННЫЕ" in m.get("content", "") for m in out)
        )

    def test_empty_facts_block_dropped(self):
        out = _coerce_history([{"role": "assistant", "content": FACTS_PREFIX + "\n   "}])
        self.assertEqual(out, [])


# ---- integration: a real stream emits established_facts after a grounding tool ----
_FAKE_SCHEMAS = [
    {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
    for n in _CODE_AGENT_BASE_TOOLS
]


@contextlib.contextmanager
def _loop_env():
    with patch.object(agent_loop, "_resolve_code_route", return_value=("test-model", 16384, None)), \
         patch.object(agent_loop, "_record_code_route_metric"), \
         patch.object(agent_loop, "build_mcp_providers", return_value=[]), \
         patch.object(ToolRegistry, "collect_schemas", return_value=list(_FAKE_SCHEMAS)), \
         patch("app.application.agent_registry.sandbox.preflight_or_raise",
               return_value={"limit": {"max_execution_seconds": 600}}):
        yield


class _ScriptedChat:
    def __init__(self, responses):
        self._r = responses
        self._i = 0

    def __call__(self, **kw):
        r = self._r[min(self._i, len(self._r) - 1)]
        self._i += 1
        return r


def _call(name, **args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def _final(text="готово"):
    return {"message": {"content": text, "tool_calls": []}}


class UngroundedFilesTest(unittest.TestCase):
    _SYS = {"role": "system", "content": "SYSTEM PROMPT — пример: calc.py, test_calc.py"}

    def test_flags_invented_file_not_grounded_ones(self):
        msgs = [
            self._SYS,  # messages[0] (system prompt) is excluded from grounding
            {"role": "system", "content": f"{FACTS_PREFIX} glob: main.py utils.py data.json"},
            {"role": "user", "content": "какие файлы?"},
        ]
        out = _ungrounded_files("В проекте: main.py, utils.py, setup.py, test_main.py.", msgs, [])
        self.assertIn("setup.py", out)
        self.assertIn("test_main.py", out)
        self.assertNotIn("main.py", out)   # grounded by the facts block
        self.assertNotIn("utils.py", out)

    def test_no_filenames_no_flag(self):
        self.assertEqual(_ungrounded_files("Проект работает, всё ок.", [self._SYS], []), [])

    def test_user_named_file_is_grounded(self):
        msgs = [self._SYS, {"role": "user", "content": "что в config.py?"}]
        self.assertEqual(_ungrounded_files("В config.py настройки.", msgs, []), [])

    def test_system_prompt_examples_do_not_ground(self):
        # calc.py lives in the system prompt (excluded) — claiming it is still flagged
        self.assertIn("calc.py", _ungrounded_files("Есть calc.py.", [self._SYS], []))

    def test_assistant_prose_does_not_self_ground(self):
        msgs = [self._SYS,
                {"role": "assistant", "content": "ранее я говорил про setup.py"},
                {"role": "user", "content": "файлы?"}]
        self.assertIn("setup.py", _ungrounded_files("Есть setup.py.", msgs, []))

    def test_established_facts_ground(self):
        out = _ungrounded_files("В requirements.txt есть pytest.", [self._SYS],
                                ["glob(**/*): requirements.txt README.md"])
        self.assertEqual(out, [])


class LoopEmitsFactsTest(unittest.TestCase):
    def tearDown(self):
        deferred_tools.clear_run("gf1")

    def test_established_facts_emitted_after_grounding_tool(self):
        chat = _ScriptedChat([_call("read_file", path="main.py"), _final("В main.py есть add и main.")])
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec",
                          return_value=SimpleNamespace(
                              status="ok",
                              output={"text": "def add(a, b): return a + b\ndef main(): ...", "ok": True})):
            evs = list(agent_loop.stream_code_agent(
                user_message="что в main.py?", project_root=tmp, run_id="gf1",
                auto_remember=False, permission_mode="bypass", chat_fn=chat,
            ))
        finals = [e for e in evs if e.get("type") == "final_response"]
        self.assertTrue(finals)
        facts = finals[-1].get("established_facts", "")
        self.assertIn("def add", facts)
        self.assertIn("read_file", facts)
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done.get("established_facts"), facts)


class GroundingNudgeLoopTest(unittest.TestCase):
    def tearDown(self):
        deferred_tools.clear_run("gn1")
        deferred_tools.clear_run("gn-external")
        deferred_tools.clear_run("gn-search-only")
        deferred_tools.clear_run("gn-fetch")
        deferred_tools.clear_run("gn-backstop")

    def test_ungrounded_file_answer_triggers_nudge_then_finalizes(self):
        # 1st answer names ungrounded files → gate nudges + continues; 2nd answer
        # (which sees the nudge) is clean → run finalizes.
        state = {"step": 0, "nudge_seen": False}

        def chat(**kw):
            if not kw.get("tools"):
                return {"message": {"content": "summary", "tool_calls": []}}
            state["step"] += 1
            if state["step"] == 1:
                return _final("В проекте есть setup.py и requirements.txt.")
            state["nudge_seen"] = any(
                "Не называй файлы по памяти" in (m.get("content") or "")
                for m in (kw.get("messages") or []))
            return _final("Проверил инструментом — таких файлов в проекте нет.")

        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            evs = list(agent_loop.stream_code_agent(
                user_message="какие файлы в проекте?", project_root=tmp, run_id="gn1",
                auto_remember=False, permission_mode="bypass", chat_fn=chat))
        self.assertTrue(state["nudge_seen"], "nudge must reach the model on the retry")
        self.assertGreaterEqual(state["step"], 2)  # the gate forced a re-answer
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "answer")

    def test_external_company_facts_without_web_are_not_finalized(self):
        state = {"step": 0, "nudge_seen": False}

        def chat(**kw):
            if not kw.get("tools"):
                return {"message": {"content": "summary", "tool_calls": []}}
            state["step"] += 1
            if state["step"] == 1:
                return _final(
                    "Laguna создана Дарио Амодеи и Дэниелом Йи в компании "
                    "Poolside в октябре 2024 года."
                )
            state["nudge_seen"] = any(
                "прочитай внешний источник" in (m.get("content") or "").lower()
                for m in (kw.get("messages") or [])
            )
            return _final(
                "Я не подтвердил создателей Laguna по внешнему источнику, "
                "поэтому не буду называть имена по памяти."
            )

        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            evs = list(agent_loop.stream_code_agent(
                user_message="Кто сделал модель Laguna и из какой она компании?",
                project_root=tmp,
                run_id="gn-external",
                auto_remember=False,
                permission_mode="bypass",
                chat_fn=chat,
            ))

        self.assertTrue(state["nudge_seen"], "runtime must require external evidence")
        self.assertGreaterEqual(state["step"], 2)
        final = [e for e in evs if e.get("type") == "final_response"][-1]["text"]
        self.assertNotIn("Дарио Амодеи", final)
        self.assertNotIn("Дэниелом Йи", final)

    def test_web_search_snippets_alone_still_require_source_read(self):
        state = {"step": 0, "nudge_seen": False}

        def chat(**kw):
            if not kw.get("tools"):
                return {"message": {"content": "summary", "tool_calls": []}}
            state["step"] += 1
            if state["step"] == 1:
                return _call("web_search", query="Laguna model founders")
            if state["step"] == 2:
                return _final("Laguna создана Дарио Амодеи в 2024 году.")
            state["nudge_seen"] = any(
                "прочитай внешний источник" in (m.get("content") or "").lower()
                for m in (kw.get("messages") or [])
            )
            return _final("Источник не найден, факт не подтвержден.")

        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec",
                          return_value=SimpleNamespace(
                              status="ok",
                              output={
                                  "text": "1. Laguna overview https://example.test/laguna",
                                  "ok": True,
                              })):
            evs = list(agent_loop.stream_code_agent(
                user_message="Кто сделал модель Laguna?",
                project_root=tmp,
                run_id="gn-search-only",
                auto_remember=False,
                permission_mode="bypass",
                chat_fn=chat,
            ))

        self.assertTrue(state["nudge_seen"])
        final = [e for e in evs if e.get("type") == "final_response"][-1]["text"]
        self.assertNotIn("Дарио Амодеи", final)

    def test_successful_web_fetch_allows_sourced_answer(self):
        chat = _ScriptedChat([
            _call("web_fetch", url="https://example.test/laguna", store=True),
            _final(
                "Согласно прочитанному источнику, Laguna выпустила Poolside: "
                "https://example.test/laguna"
            ),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec",
                          return_value=SimpleNamespace(
                              status="ok",
                              output={
                                  "text": (
                                      "[web_fetch: https://example.test/laguna] "
                                      "Poolside released Laguna."
                                  ),
                                  "ok": True,
                              })):
            evs = list(agent_loop.stream_code_agent(
                user_message="Кто сделал модель Laguna?",
                project_root=tmp,
                run_id="gn-fetch",
                auto_remember=False,
                permission_mode="bypass",
                chat_fn=chat,
            ))

        final = [e for e in evs if e.get("type") == "final_response"][-1]["text"]
        self.assertIn("https://example.test/laguna", final)
        self.assertNotIn("Не могу подтвердить фактический ответ", final)

    def test_unsupported_external_claim_is_replaced_after_one_retry(self):
        chat = _ScriptedChat([
            _final("Компания основана Иваном Ивановым в 2025 году."),
            _final("Компания основана Иваном Ивановым в 2025 году."),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            evs = list(agent_loop.stream_code_agent(
                user_message="Проверь в интернете, кто основал эту компанию.",
                project_root=tmp,
                run_id="gn-backstop",
                auto_remember=False,
                permission_mode="bypass",
                chat_fn=chat,
            ))

        final = [e for e in evs if e.get("type") == "final_response"][-1]["text"]
        self.assertIn("не был успешно прочитан внешний источник", final)
        self.assertNotIn("Иваном Ивановым", final)
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertTrue(done["ok"])
        self.assertEqual(done["stop_reason"], "answer")


if __name__ == "__main__":
    unittest.main()
