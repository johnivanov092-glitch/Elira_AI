"""A completed read is followed by the model's own next turn; runtime adds no closing cue.

A trailing user-role "[Следующий шаг: инструкция runtime, не выводи] …" block made
Qwen3.8 (vLLM :8011) treat its finished answer as reasoning, emit </think> and write
the whole answer a second time (A/B 2026-10-06: 8/8 doubled with the cue, 0/8 without).
"""
from __future__ import annotations

from app.application.code_agent.agent_loop import request_cancel, stream_code_agent
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.run_journal import RunJournal
from webskill.application.code_agent.tools import _web
from webskill.application.web_evidence.receipts import excerpt_sources, format_source
from webskill.infrastructure.search.web_runtime import PageFetchResult


URL = "https://example.org/first"
OTHER = "https://example.org/second"
BODY = "The observation was registered on 14 September 2015."
CUE_ID = "web-closing-context"


def _call(name, arguments, call_id="tool"):
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def _no_cue(messages):
    assert not any(message.get("_msg_id") == CUE_ID for message in messages)
    assert not any("Следующий шаг: инструкция runtime" in str(message.get("content") or "")
                   for message in messages)


def test_read_is_followed_by_the_model_turn_without_a_runtime_cue(tmp_path, monkeypatch):
    reads, main = [], []
    def fetch(url, limit, **kwargs):
        reads.append(url)
        return PageFetchResult(text=BODY, final_url=url)
    monkeypatch.setattr(_web, "_fetch_one", fetch)
    def chat(**kwargs):
        main.append(kwargs)
        _no_cue(kwargs["messages"])
        if len(main) == 1:
            return {"message": {"tool_calls": [_call("web_fetch", {"url": URL})]}}
        # The read result is the last input: nothing is appended on the user side.
        assert kwargs["messages"][-1]["role"] == "tool"
        assert any(message.get("role") == "user" and
                   "When was the observation registered?" in message.get("content", "")
                   for message in kwargs["messages"])
        assert any(tool["function"]["name"] == "web_fetch" for tool in kwargs["tools"])
        return {"message": {"content": "The observation was registered on 14 September 2015."}}
    events = list(stream_code_agent(user_message="When was the observation registered?",
        project_root=tmp_path, chat_fn=chat, base_tools=["web_fetch"], auto_remember=False,
        permission_mode="bypass", num_ctx=65536))
    assert reads == [URL] and len(main) == 2
    assert events[-1]["stop_reason"] == "answer"
    assert not any(event["type"] == "answer_grounding" for event in events)
    assert next(event for event in events if event["type"] == "final_response")["answer_status"] == "complete"
    assert not any(event["type"] == "answer_format_correction" for event in events)


def test_another_source_stays_available_after_a_read(tmp_path, monkeypatch):
    reads, main = [], []
    monkeypatch.setattr(_web, "_fetch_one", lambda url, limit, **kwargs: (
        reads.append(url) or PageFetchResult(text=BODY if url == URL else "The second source names two detectors.",
                                           final_url=url)))
    def chat(**kwargs):
        main.append(kwargs["messages"])
        _no_cue(kwargs["messages"])
        if len(main) == 1:
            return {"message": {"tool_calls": [_call("web_fetch", {"url": URL})]}}
        assert any(tool["function"]["name"] == "web_fetch" for tool in kwargs["tools"])
        if len(main) == 2:
            return {"message": {"tool_calls": [_call("web_fetch", {"url": OTHER}, "second")]}}
        assert sum(message.get("role") == "tool" for message in kwargs["messages"]) == 2
        return {"message": {"content": "Registered on 14 September 2015; two detectors were named."}}
    events = list(stream_code_agent(user_message="Find the observation date and the detector count.",
        project_root=tmp_path, chat_fn=chat, base_tools=["web_fetch"], auto_remember=False,
        permission_mode="bypass", num_ctx=65536))
    assert reads == [URL, OTHER] and len(main) == 3 and events[-1]["stop_reason"] == "answer"


def test_code_goal_continues_to_real_file_action_after_web_read(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(_web, "_fetch_one", lambda url, limit: PageFetchResult(text=BODY, final_url=url))
    def chat(**kwargs):
        calls.append(kwargs["messages"])
        _no_cue(kwargs["messages"])
        if len(calls) == 1:
            return {"message": {"tool_calls": [_call("web_fetch", {"url": URL})]}}
        if len(calls) == 2:
            assert any("создай note.txt" in message.get("content", "") for message in kwargs["messages"])
            assert any(tool["function"]["name"] == "write_file" for tool in kwargs["tools"])
            return {"message": {"tool_calls": [_call("write_file", {"path": "note.txt", "content": BODY})]}}
        return {"message": {"content": "Создан note.txt с прочитанной датой."}}
    events = list(stream_code_agent(user_message="Прочитай источник и создай note.txt с датой.",
        project_root=tmp_path, chat_fn=chat, base_tools=["web_fetch", "write_file"],
        auto_remember=False, permission_mode="bypass", num_ctx=65536))
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == BODY
    assert len(calls) == 3 and events[-1]["stop_reason"] == "answer"


def test_read_handles_require_actual_verified_text(tmp_path):
    source = excerpt_sources(run_id="closing", tool="web_fetch", url=URL, text=BODY, fetched_at=1.0)[0]
    evidence = RunEvidence(sources=[source])
    marker_only = [{"role": "tool", "content": f"[[source:{source['id']}]]"}]
    assert evidence.read_source_handles(marker_only) == ()
    result = {"role": "tool", "content": format_source(source)}
    assert evidence.read_source_handles([result]) == (source["id"],)
