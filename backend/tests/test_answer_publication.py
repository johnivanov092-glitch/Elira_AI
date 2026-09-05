import pytest

from app.application.code_agent import agent_loop


@pytest.mark.parametrize("abort", ["error", "cancel"])
def test_interrupted_stream_never_accepts_draft_or_remembers_it(tmp_path, abort, monkeypatch):
    run_id = f"unchecked-draft-{abort}"
    draft = "Непроверенное утверждение, которое не должно попасть в ответ."
    remembered = []
    monkeypatch.setattr(agent_loop, "_try_remember_turn", lambda **kwargs: remembered.append(kwargs))

    def stream(**kwargs):
        yield {"type": "delta", "content": draft}
        if abort == "error":
            raise RuntimeError("provider disconnected")
        agent_loop.request_cancel(run_id)

    events = list(agent_loop.stream_code_agent(
        user_message="Расскажи подробнее", project_root=tmp_path, run_id=run_id,
        chat_fn=lambda **kwargs: {}, chat_stream_fn=stream,
        auto_remember=True, base_tools=["capability_load"],
    ))

    assert not any(event["type"] == "final_response" for event in events)
    assert all(event.get("answer_state") == "draft" for event in events if event["type"] == "delta")
    assert remembered == []
    assert events[-1]["type"] == "done"
    assert events[-1]["stop_reason"] == ("error" if abort == "error" else "cancelled")


def test_accepted_answer_keeps_reasoning_separate_and_uses_final_provider_text(tmp_path):
    def stream(**kwargs):
        yield {"type": "reasoning", "content": "Отдельное рассуждение."}
        yield {"type": "delta", "content": "Предварительная формулировка."}
        yield {"type": "message", "response": {"message": {
            "content": "Рада поболтать.", "tool_calls": [],
        }}}

    events = list(agent_loop.stream_code_agent(
        user_message="Поболтаем", project_root=tmp_path,
        chat_fn=lambda **kwargs: {}, chat_stream_fn=stream,
        auto_remember=False, base_tools=["capability_load"], thinking=True,
    ))

    visible = [event for event in events if event["type"] in {
        "reasoning_delta", "delta", "final_response",
    }]
    assert [event["type"] for event in visible] == ["reasoning_delta", "delta", "final_response"]
    assert visible[0]["text"] == "Отдельное рассуждение."
    assert visible[1]["text"] == "Предварительная формулировка."
    assert visible[1]["answer_state"] == "draft"
    assert visible[2]["text"] == "Рада поболтать."
    assert visible[2]["answer_state"] == "accepted"


def test_conversation_delta_is_visible_before_model_finishes(tmp_path):
    import threading

    release = threading.Event()
    def stream(**kwargs):
        yield {"type": "delta", "content": "Живой разговор поступает до завершения генерации. " * 3}
        assert release.wait(3), "UI did not receive the draft while provider was running"
        yield {"type": "message", "response": {"message": {"content": "Рада поговорить.", "tool_calls": []}}}

    events = []
    for event in agent_loop.stream_code_agent(
        user_message="Поболтаем", project_root=tmp_path, chat_fn=lambda **kwargs: {},
        chat_stream_fn=stream, auto_remember=False, base_tools=["capability_load"],
    ):
        events.append(event)
        if event["type"] == "delta":
            release.set()
    assert events[-1]["stop_reason"] == "answer"
