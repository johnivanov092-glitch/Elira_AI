from __future__ import annotations

import tempfile
import threading
import time
import unittest

from app.application.code_agent import agent_loop
from app.application.code_agent.agent_loop import stream_code_agent, submit_answer


def _ask_chat(question="К какому хосту подключиться?", options=None, run_id_holder=None):
    """chat_fn: first tool-enabled call asks the user, then finalizes with the
    answer echoed so tests can assert the answer reached the model."""
    state = {"asked": False}

    def chat_fn(**kw):
        if not kw.get("tools"):
            return {"message": {"content": "summary", "tool_calls": []}}
        if not state["asked"]:
            state["asked"] = True
            args = {"question": question}
            if options is not None:
                args["options"] = options
            return {"message": {"content": "", "tool_calls": [{
                "function": {"name": "ask_user", "arguments": args},
            }]}}
        # Echo the last tool message (the answer) so we can check it arrived.
        last_tool = next(
            (m for m in reversed(kw.get("messages") or []) if m.get("role") == "tool"), {})
        return {"message": {"content": f"OK. Видел ответ: {last_tool.get('content','')}", "tool_calls": []}}

    return chat_fn


def _always_ask_chat(question="К какому хосту подключиться?"):
    """chat_fn that NEVER finalizes on its own — it re-emits the SAME ask_user
    every tool-enabled turn, so the run must terminate via ask_user's own bound."""
    def chat_fn(**kw):
        if not kw.get("tools"):
            return {"message": {"content": "итоговый ответ", "tool_calls": []}}
        return {"message": {"content": "", "tool_calls": [{
            "function": {"name": "ask_user", "arguments": {"question": question}},
        }]}}
    return chat_fn


def _find_qid(events):
    for e in events:
        if e.get("type") == "question_pending":
            return e["question_id"], e
    return None, None


class AskUserTest(unittest.TestCase):
    def test_question_pending_emitted_with_options(self):
        # Answer from another thread shortly after the run starts.
        def answer_soon():
            deadline = time.monotonic() + 25  # cover cold-start registration
            while time.monotonic() < deadline:
                with agent_loop._QUESTION_LOCK:
                    qids = [q for q, a in agent_loop._QUESTION_ANSWERS.items() if a is None]
                if qids:
                    submit_answer(qids[0], "ai-server")
                    return
                time.sleep(0.02)

        t = threading.Thread(target=answer_soon, daemon=True)
        t.start()
        with tempfile.TemporaryDirectory() as tmp:
            evs = list(stream_code_agent(
                user_message="подключись к ssh", project_root=tmp, model="test-model",
                max_steps=6, chat_fn=_ask_chat(options=["server-1", "ai-server"]),
                run_id="ask1", approval_wait_seconds=30, auto_remember=False,
            ))
        t.join(timeout=2)
        qid, qevent = _find_qid(evs)
        self.assertIsNotNone(qid)
        self.assertEqual(qevent["options"], ["server-1", "ai-server"])
        # the answer became the tool result and reached the model's final answer
        finals = [e for e in evs if e.get("type") == "final_response"]
        self.assertIn("ai-server", finals[-1]["text"])

    def test_timeout_does_not_kill_run(self):
        # No answer, tiny wait budget → run continues with a "not answered" note.
        with tempfile.TemporaryDirectory() as tmp:
            evs = list(stream_code_agent(
                user_message="подключись", project_root=tmp, model="test-model",
                max_steps=6, chat_fn=_ask_chat(), run_id="ask-timeout",
                approval_wait_seconds=1, auto_remember=False,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "answer")  # NOT killed
        finals = [e for e in evs if e.get("type") == "final_response"]
        self.assertIn("не ответил", finals[-1]["text"])

    def test_submit_answer_unknown_id_is_false(self):
        self.assertFalse(submit_answer("nope-not-real", "x"))

    def test_no_questions_never_pauses(self):
        # «Не спрашивать» toggle: ask_user is answered inline with a "decide for
        # yourself" note — no question_pending, no human wait, run continues.
        with tempfile.TemporaryDirectory() as tmp:
            evs = list(stream_code_agent(
                user_message="подключись к ssh", project_root=tmp, model="test-model",
                max_steps=6, chat_fn=_ask_chat(options=["server-1", "ai-server"]),
                run_id="ask-noq", approval_wait_seconds=30, auto_remember=False,
                no_questions=True,
            ))
        # The run never paused for the human...
        self.assertEqual([e for e in evs if e.get("type") == "question_pending"], [])
        self.assertEqual([e for e in evs if e.get("type") == "question_wait"], [])
        # ...but the tool result told the model to decide for itself and it finished.
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "answer")
        finals = [e for e in evs if e.get("type") == "final_response"]
        self.assertIn("не задавать вопросы", finals[-1]["text"])

    def test_no_questions_persistent_reask_finalizes_not_loop_guard(self):
        # A weak model re-emits the SAME ask_user every step in no_questions mode.
        # The canned reply gives it nothing to diverge on, but the run must NOT die
        # as loop_guard — ask_user's own bound finalizes it cleanly, well under
        # max_steps. (Regression: ask_user is exempt from the generic loop-guard.)
        bound = agent_loop._ASK_USER_MAX + agent_loop._ASK_USER_OVER_CAP_GRACE
        with tempfile.TemporaryDirectory() as tmp:
            evs = list(stream_code_agent(
                user_message="подключись к ssh", project_root=tmp, model="test-model",
                max_steps=50, chat_fn=_always_ask_chat(), run_id="ask-persist-noq",
                approval_wait_seconds=5, auto_remember=False, no_questions=True,
            ))
        self.assertEqual([e for e in evs if e.get("type") == "question_pending"], [])
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "answer")  # NOT loop_guard
        self.assertLessEqual(done["steps"], bound + 1)

    def test_persistent_reask_finalizes_not_loop_guard(self):
        # Same latent bug on the NORMAL path: model re-asks the same question
        # forever, human never answers. After the budget (3 timed-out pauses +
        # over-cap nudges) it finalizes cleanly at ask_user's bound — not via the
        # generic loop_guard error.
        bound = agent_loop._ASK_USER_MAX + agent_loop._ASK_USER_OVER_CAP_GRACE
        with tempfile.TemporaryDirectory() as tmp:
            evs = list(stream_code_agent(
                user_message="подключись", project_root=tmp, model="test-model",
                max_steps=50, chat_fn=_always_ask_chat(), run_id="ask-persist",
                approval_wait_seconds=1, auto_remember=False,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "answer")
        # exactly _ASK_USER_MAX human pauses were offered before the bound kicked in
        pends = [e for e in evs if e.get("type") == "question_pending"]
        self.assertEqual(len(pends), agent_loop._ASK_USER_MAX)
        self.assertLessEqual(done["steps"], bound + 1)


class AnswerRouteTest(unittest.TestCase):
    def test_answer_route_404_on_unknown(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.code_agent_routes import router
        app = FastAPI()
        app.include_router(router)
        r = TestClient(app).post("/api/code-agent/questions/ghost/answer", json={"answer": "x"})
        self.assertEqual(r.status_code, 404)

    def test_answer_route_delivers_registered(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.code_agent_routes import router
        # register a pending question directly
        with agent_loop._QUESTION_LOCK:
            agent_loop._QUESTION_ANSWERS["qX"] = None
        try:
            app = FastAPI()
            app.include_router(router)
            r = TestClient(app).post("/api/code-agent/questions/qX/answer", json={"answer": "ai-server"})
            self.assertEqual(r.status_code, 200)
            with agent_loop._QUESTION_LOCK:
                self.assertEqual(agent_loop._QUESTION_ANSWERS["qX"], "ai-server")
        finally:
            with agent_loop._QUESTION_LOCK:
                agent_loop._QUESTION_ANSWERS.pop("qX", None)


if __name__ == "__main__":
    unittest.main()
