"""A completed read gets one closing opportunity through the normal coordinator."""
from __future__ import annotations

from app.application.code_agent.agent_loop import request_cancel, stream_code_agent
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.task_guidance import WEB_SOURCE_FIDELITY_GUIDANCE
from app.application.code_agent.tools import _web
from app.application.code_agent.turn_context import TurnContext
from app.application.web_evidence.receipts import excerpt_sources, format_source
from app.infrastructure.search.web_runtime import PageFetchResult


URL = "https://example.org/first"
OTHER = "https://example.org/second"
BODY = "The observation was registered on 14 September 2015."
CUE_ID = "web-closing-context"


def _call(name, arguments, call_id="tool"):
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def _cue(messages):
    cues = [message for message in messages if message.get("_msg_id") == CUE_ID]
    assert len(cues) == 1 and messages[-1] is cues[0] and cues[0]["role"] == "user"
    return cues[0]["content"]


def test_read_context_cues_next_main_answer_without_an_extra_turn(tmp_path, monkeypatch):
    reads, main = [], []
    def fetch(url, limit, **kwargs):
        reads.append(url)
        return PageFetchResult(text=BODY, final_url=url)
    monkeypatch.setattr(_web, "_fetch_one", fetch)
    def chat(**kwargs):
        main.append(kwargs)
        if len(main) == 1:
            assert not any(message.get("_msg_id") == CUE_ID for message in kwargs["messages"])
            return {"message": {"tool_calls": [_call("web_fetch", {"url": URL})]}}
        cue = _cue(kwargs["messages"])
        assert "When was the observation registered?" not in cue
        assert any(message.get("role") == "user" and
                   "When was the observation registered?" in message.get("content", "")
                   for message in kwargs["messages"][:-1])
        assert f"Прочитаны страницы: {URL}." in cue and "[[source:w_" not in cue and BODY not in cue
        assert "итоговый ответ" in cue and "Промежуточная сводка не требуется" in cue
        assert cue.count(WEB_SOURCE_FIDELITY_GUIDANCE) == 1
        assert "условия применимости" in cue and "неопределённость и логическую связь" in cue
        assert "прямое измерение от расчётной оценки" in cue
        assert "Частичный поиск не доказывает общего отсутствия" in cue
        assert "не начинай ради них новый поиск" in cue
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


def test_closing_cue_keeps_another_source_available_and_does_not_accumulate(tmp_path, monkeypatch):
    reads, main = [], []
    monkeypatch.setattr(_web, "_fetch_one", lambda url, limit, **kwargs: (
        reads.append(url) or PageFetchResult(text=BODY if url == URL else "The second source names two detectors.",
                                           final_url=url)))
    def chat(**kwargs):
        main.append(kwargs["messages"])
        if len(main) == 1:
            return {"message": {"tool_calls": [_call("web_fetch", {"url": URL})]}}
        cue = _cue(kwargs["messages"])
        assert "другие источники доступны" in cue
        assert cue.count(WEB_SOURCE_FIDELITY_GUIDANCE) == 1
        assert any(tool["function"]["name"] == "web_fetch" for tool in kwargs["tools"])
        if len(main) == 2:
            return {"message": {"tool_calls": [_call("web_fetch", {"url": OTHER}, "second")]}}
        assert URL in cue and OTHER in cue and "[[source:w_" not in cue
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
        if len(calls) == 1:
            return {"message": {"tool_calls": [_call("web_fetch", {"url": URL})]}}
        if len(calls) == 2:
            cue = _cue(kwargs["messages"])
            assert "чтение его не заменяет" in cue
            assert any("создай note.txt" in message.get("content", "")
                       for message in kwargs["messages"][:-1])
            assert any(tool["function"]["name"] == "write_file" for tool in kwargs["tools"])
            return {"message": {"tool_calls": [_call("write_file", {"path": "note.txt", "content": BODY})]}}
        assert not any(message.get("_msg_id") == CUE_ID for message in kwargs["messages"])
        return {"message": {"content": "Создан note.txt с прочитанной датой."}}
    events = list(stream_code_agent(user_message="Прочитай источник и создай note.txt с датой.",
        project_root=tmp_path, chat_fn=chat, base_tools=["web_fetch", "write_file"],
        auto_remember=False, permission_mode="bypass", num_ctx=65536))
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == BODY
    assert len(calls) == 3 and events[-1]["stop_reason"] == "answer"


def test_declared_artifact_contract_suppresses_ordinary_answer_cue(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(_web, "_fetch_one", lambda url, limit: PageFetchResult(text=BODY, final_url=url))
    def chat(**kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            return {"message": {"tool_calls": [_call("runtime_control", {"operation": "task_decide",
                "config": {"disposition": "one_off", "reason": "Write the requested local report.",
                           "targets": [str(tmp_path / "report.txt")]}})]}}
        if len(calls) == 2:
            return {"message": {"tool_calls": [_call("web_fetch", {"url": URL})]}}
        assert not any(message.get("_msg_id") == CUE_ID for message in kwargs["messages"])
        assert request_cancel("web-closing-artifact")
        return {"message": {"content": ""}}
    events = list(stream_code_agent(user_message="Find the observation date and produce report.txt.",
        project_root=tmp_path, chat_fn=chat, run_id="web-closing-artifact",
        base_tools=["runtime_control", "web_fetch"], auto_remember=False,
        permission_mode="bypass", num_ctx=65536))
    assert len(calls) == 3 and events[-1]["stop_reason"] == "cancelled"
    assert not any(event["type"] == "final_response" for event in events)
    assert RunJournal.load("web-closing-artifact").state["task_outcome"]["artifact_contract_seen"]


def test_cue_requires_actual_verified_text_and_waits_for_all_tool_results(tmp_path):
    source = excerpt_sources(run_id="closing", tool="web_fetch", url=URL, text=BODY, fetched_at=1.0)[0]
    evidence = RunEvidence(sources=[source])
    marker_only = [{"role": "tool", "content": f"[[source:{source['id']}]]"}]
    assert evidence.read_source_handles(marker_only) == ()
    result = {"role": "tool", "content": format_source(source)}
    handles = evidence.read_source_handles([result])
    assert handles == (source["id"],)
    pages = evidence.read_source_pages([result])
    assert pages == (URL,)
    messages = [{"role": "system", "content": "System"},
                {"role": "assistant", "content": "", "tool_calls": [
                    _call("web_fetch", {"url": URL}, "first"), _call("web_fetch", {"url": OTHER}, "second")]},
                result]
    context = TurnContext(messages=messages, raw_user_message="Find the date.",
                          root=tmp_path, working_dir=None, run_id="closing")
    assert context.refresh_web_closing_context(messages, read_pages=pages) == messages
    messages.append({"role": "tool", "content": "Other result"})
    first = context.refresh_web_closing_context(messages, read_pages=pages)
    second = context.refresh_web_closing_context(first, read_pages=pages)
    assert first == second and len(second) == len(messages) + 1
    assert _cue(second)
    assert context.refresh_web_closing_context(second, read_pages=()) == messages


def test_prepare_rebuilds_closing_handles_after_packed_source_restoration(tmp_path):
    sources = [excerpt_sources(run_id="closing", tool="web_fetch", url=url, text=BODY, fetched_at=1.0)[0]
               for url in (URL, OTHER)]
    evidence = RunEvidence(sources=sources)
    context = TurnContext(messages=[{"role": "system", "content": "System"}],
        raw_user_message="Find the date.", root=tmp_path, working_dir=None, run_id="closing")
    context.guidance_message_ids = set()
    def prepare(messages, **kwargs):
        # A packing pass drops one former excerpt; restoration provides only
        # the other. The latest cue cannot continue naming the omitted one.
        packed = kwargs["restore_messages"](messages, compacted=True)
        return packed, True, {"current_tokens": 200}
    def restore(messages, **kwargs):
        return [*messages, {"role": "user", "content": format_source(sources[1]),
                            "_msg_id": "web-source-context"}]
    context.prepare(prepare_fn=prepare, num_ctx=65536, model="test", chat_fn=None,
        context_profile={"reserved_output_tokens": 2048}, tool_schemas=[], cancel_handle=None, audit_sink=None,
        restore_source_context=restore, web_closing_sources=evidence.read_source_pages)
    cue = _cue(context.messages)
    assert OTHER in cue and URL not in cue


def test_soft_closing_cue_is_omitted_without_displacing_source_context(tmp_path):
    source = excerpt_sources(run_id="closing", tool="web_fetch", url=URL, text=BODY, fetched_at=1.0)[0]
    evidence = RunEvidence(sources=[source])
    messages = [{"role": "system", "content": "System"},
                {"role": "tool", "name": "web_fetch", "content": format_source(source)}]
    context = TurnContext(messages=messages, raw_user_message="Find the date.",
                          root=tmp_path, working_dir=None, run_id="closing")
    context.guidance_message_ids = set()
    def prepare(packed, **kwargs):
        return kwargs["restore_messages"](packed, compacted=False), False, {}
    context.prepare(prepare_fn=prepare, num_ctx=8192, model="test", chat_fn=None,
        context_profile={"reserved_output_tokens": 2048, "safe_input_budget": 1},
        tool_schemas=[], cancel_handle=None, audit_sink=None,
        restore_source_context=lambda packed, **kwargs: packed,
        web_closing_sources=evidence.read_source_pages)
    assert context.messages == messages
    assert evidence.read_source_handles(context.messages) == (source["id"],)
