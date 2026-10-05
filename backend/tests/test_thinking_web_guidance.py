"""Thinking uses the normal web executor and preserves source evidence."""
from copy import deepcopy

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.delivery_session import build_continuation_kwargs
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.task_guidance import WEB_SOURCE_FIDELITY_GUIDANCE, task_guidance_blocks
from app.application.code_agent.tools import _web
from app.infrastructure.llm.openai_compatible import _normalize_messages_for_request
from app.infrastructure.search import web_search
from app.infrastructure.search.web_runtime import PageFetchResult


def test_retrieval_guidance_keeps_source_fidelity_and_operational_contracts():
    blocks = task_guidance_blocks({"web_search", "web_fetch", "web_query", "browser", "http_api"})
    assert set(blocks) == {"web"}
    guidance = blocks["web"]
    assert guidance.count(WEB_SOURCE_FIDELITY_GUIDANCE) == 1
    assert "API либо локального файла" in guidance
    assert "предмет, группу, условия применимости" in guidance
    assert "Сохраняй отрицания и силу вывода" in guidance
    assert "Дата публикации, индексации или чтения не доказывает дату события" in guidance
    assert "обнаружение, а не содержание" in guidance and "[[source:id]]" in guidance
    assert "первичные источники" in guidance and "текущим официальным индексом нужного канала" in guidance
    assert "в той же ветке и компоненте" in guidance and "учитывай backport" in guidance
    assert "предупреждения поисковых движков" in guidance
    assert "web_fetch(store=true)" in guidance and "web_query" in guidance and "store=false" in guidance
    assert "force_refresh — только по прямому запросу" in guidance
    assert "find работает для HTML, текста и PDF" in guidance and "используй browser" in guidance


def test_thinking_continues_through_web_tools_and_retains_citations_on_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    source_url = "https://example.org/fixture-documentation"
    browser_url = "https://example.org/fixture-demo"
    excerpt = "Fixture documentation: the example accepts UTF-8 JSON input. " * 6
    monkeypatch.setattr(web_search, "search_web", lambda *_args, **_kwargs: {
        "sources": [{"title": "Fixture documentation", "href": source_url, "body": "Input format example."}],
        "engines_used": ["fixture"],
    })
    monkeypatch.setattr(_web, "_fetch_one", lambda url, _limit: PageFetchResult(
        text=excerpt, final_url=url, status_code=200,
    ))
    monkeypatch.setattr("app.application.web.ssrf_guard.check_ssrf", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_web, "_browser_render", lambda *_args: (
        "Fixture demo", browser_url, "The rendered fixture shows the input example.", 0, None, 200,
    ))
    sequence = [
        ("capability_load", {"group": "web"}),
        ("web_search", {"query": "fixture documentation input format"}),
        ("web_fetch", {"url": source_url}),
        ("browser", {"url": browser_url}),
    ]
    captures = []
    events = []
    reasoning = "Synthetic reasoning stream fixture."

    def chat_stream(**kwargs):
        assert kwargs["options"]["reasoning_effort"] == "xhigh"
        assert kwargs["options"]["chat_template_kwargs"] == {
            "enable_thinking": True, "reasoning_effort": "xhigh",
        }
        captures.append({"messages": deepcopy(kwargs["messages"])})
        index = len(captures) - 1
        if index:
            names = {schema["function"]["name"] for schema in kwargs["tools"]}
            assert {"web_search", "web_fetch", "browser"} <= names
            assert any(WEB_SOURCE_FIDELITY_GUIDANCE in item.get("content", "")
                       for item in kwargs["messages"])
        if index < len(sequence):
            name, arguments = sequence[index]
            message = {"content": "", "tool_calls": [{
                "id": f"fixture-call-{index}", "function": {"name": name, "arguments": arguments},
            }]}
        else:
            context = "\n".join(item.get("content", "") for item in kwargs["messages"])
            assert excerpt.strip() in context
            assert browser_url in context
            assert "The rendered fixture shows the input example." in context
            cue = kwargs["messages"][-1]
            assert cue["role"] == "user" and cue["_msg_id"] == "web-closing-context"
            assert cue["content"].count(WEB_SOURCE_FIDELITY_GUIDANCE) == 1
            assert excerpt.strip() not in cue["content"]
            sources = {source["url"]: source for event in events if event["type"] == "tool_call"
                       for source in event.get("sources", []) if source["status"] == "excerpt"}
            marker = sources[source_url]["id"]
            browser_marker = sources[browser_url]["id"]
            message = {"content": (
                f"Example accepts UTF-8 JSON. [Fixture documentation]({source_url}) [[source:{marker}]]\n"
                f"The demo shows its input example. [Fixture demo]({browser_url}) [[source:{browser_marker}]]"
            ), "tool_calls": []}
        yield {"type": "reasoning", "content": reasoning}
        yield {"type": "message", "response": {"message": {**message, "reasoning_content": reasoning}}}

    for event in stream_code_agent(
        user_message="Проверь формат в документации и открой пример; приведи источник.",
        project_root=tmp_path, session_id="thinking-web-chat", run_id="thinking-web-run",
        model="test-model", num_ctx=131072, base_tools=["capability_load"], thinking=True,
        chat_fn=lambda **_kwargs: {}, chat_stream_fn=chat_stream, auto_remember=False,
    ):
        events.append(event)
    assert len(captures) == 5
    calls = [event for event in events if event["type"] == "tool_call"]
    assert [event["tool"] for event in calls] == [name for name, _arguments in sequence]
    assert all(event["ok"] for event in calls)
    assert len([event for event in events if event["type"] == "reasoning_delta"]) == 5
    for captured in captures:
        wire = _normalize_messages_for_request(captured["messages"])
        assert all(reasoning not in item.get("content", "") for item in wire)
        for index, message in enumerate(wire):
            if message.get("tool_calls"):
                following = wire[index + 1:index + 1 + len(message["tool_calls"])]
                assert all(item["role"] == "tool" for item in following)
    final = next(event for event in events if event["type"] == "final_response")
    assert final["source_status"] == "matched"
    assert f"[Fixture documentation]({source_url})" in final["text"]
    assert f"[Fixture demo]({browser_url})" in final["text"]
    assert {citation["source"]["url"] for citation in final["citations"]} == {source_url, browser_url}
    source = final["citations"][0]["source"]
    assert source["url"] == source_url
    browser_source = next(citation["source"] for citation in final["citations"]
                          if citation["source"]["url"] == browser_url)
    assert final["citations"][0]["claim_support"] == "not_assessed"
    state = RunJournal.load("thinking-web-run").state
    assert any(item["excerpt_hash"] == source["excerpt_hash"] for item in state["web_sources"])
    assert any(item["url"] == browser_url and item["status"] == "excerpt" for item in state["web_sources"])

    def continued(**kwargs):
        assert kwargs["options"]["chat_template_kwargs"]["enable_thinking"] is True
        context = "\n".join(item.get("content", "") for item in kwargs["messages"])
        assert source["quote"] in context
        assert source_url in context
        assert browser_source["quote"] in context
        assert browser_url in context
        assert WEB_SOURCE_FIDELITY_GUIDANCE in kwargs["messages"][-1]["content"]
        return {"message": {"content": (
            f"Input format remains grounded in the source. [[source:{source['id']}]]\n"
            f"The rendered example is retained. [[source:{browser_source['id']}]]"
        ), "tool_calls": []}}

    resumed = list(stream_code_agent(**build_continuation_kwargs("thinking-web-run", chat_fn=continued)))
    resumed_final = next(event for event in resumed if event["type"] == "final_response")
    assert resumed_final["source_status"] == "matched"
    assert resumed_final["citations"][0]["source"]["excerpt_hash"] == source["excerpt_hash"]
    assert {citation["source"]["url"] for citation in resumed_final["citations"]} == {source_url, browser_url}
