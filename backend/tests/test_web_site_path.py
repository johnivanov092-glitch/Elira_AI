"""John's web path: the model answers in prose; 2 sites → enough? : up to 5 → answer."""

from copy import deepcopy
import re

import pytest

from app.application.code_agent import agent_loop
from app.application.code_agent.answer_contracts import explicit_web_site_limit
from app.application.code_agent.tools import _web
from app.infrastructure.search.web_runtime import PageFetchResult


URLS = [f"https://example.org/page{index}" for index in range(1, 8)]
TEXTS = {url: f"Факт номер {index}: на странице {index} указано значение {index * 10}."
         for index, url in enumerate(URLS, 1)}
ANSWER_TURN = "[Ответ по прочитанному]"


@pytest.fixture
def reads(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr("app.infrastructure.llm.openai_compatible.server_context_window",
                        lambda *, fresh=True: 65536)
    fetched = []

    def fetch(url, limit):
        fetched.append(url)
        return PageFetchResult(text=TEXTS[url], final_url=url, status_code=200)

    monkeypatch.setattr(_web, "_fetch_one", fetch)
    return fetched


def snapshot(kwargs):
    return {"messages": deepcopy(kwargs["messages"]), "tools": deepcopy(kwargs["tools"]),
            "options": deepcopy({key: value for key, value in kwargs["options"].items()
                                  if key != "_stream_cancel_handle"})}


def fetch_call(*urls, call_id="read"):
    arguments = {"urls": list(urls)} if len(urls) > 1 else {"url": urls[0]}
    return {"id": call_id + "-" + str(len(urls)), "function": {"name": "web_fetch", "arguments": arguments}}


def turn(*calls, content=""):
    return {"message": {"content": content, "tool_calls": list(calls)}}


def source_id(messages, quote):
    for message in messages:
        content = message.get("content") or ""
        markers = list(re.finditer(r"\[\[source:([a-zA-Z0-9_-]{1,80})\]\]", content))
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(content)
            if quote in content[marker.end():end]:
                return marker[1]
    pytest.fail("Expected the read excerpt and its source marker in provider context")


def run(tmp_path, chat=None, message="Найди значение и объясни, что оно значит.", **kwargs):
    return list(agent_loop.stream_code_agent(user_message=message, project_root=tmp_path,
        num_ctx=65536, base_tools=["web_fetch", "web_search"], permission_mode="bypass",
        auto_remember=False, chat_fn=chat, **kwargs))


def final(events):
    return next(event for event in events if event["type"] == "final_response")


def tool_texts(messages):
    return [message.get("content") or "" for message in messages if message.get("role") == "tool"]


def test_model_prose_with_read_source_is_the_answer_by_default(tmp_path, reads, monkeypatch):
    calls = []

    def native(**kwargs):
        calls.append(snapshot(kwargs))
        if len(calls) == 1:
            yield {"type": "message", "response": turn(fetch_call(URLS[0]))}
            return
        prose = ("Значение равно 10, то есть страница указывает его прямо. "
                 f"[[source:{source_id(kwargs['messages'], TEXTS[URLS[0]])}]]")
        yield {"type": "delta", "content": prose}
        yield {"type": "message", "response": turn(content=prose)}

    monkeypatch.setattr(agent_loop, "_local_chat_stream", native)
    events = run(tmp_path)
    answer = final(events)
    assert len(calls) == 2 and all(call["tools"] for call in calls)
    assert all("response_format" not in call["options"] for call in calls)
    assert answer["text"].startswith("Значение равно 10, то есть")
    assert "Из прочитанных источников" not in answer["text"]
    assert answer["answer_status"] == "complete" and answer["source_status"] == "matched"
    assert [event for event in events if event["type"] == "delta"]


def test_after_second_site_the_model_decides_whether_it_is_enough(tmp_path, reads):
    calls = []

    def chat(**kwargs):
        calls.append(snapshot(kwargs))
        if len(calls) == 1:
            return turn(fetch_call(URLS[0]))
        if len(calls) == 2:
            assert not any("Прочитано сайтов" in text for text in tool_texts(kwargs["messages"]))
            return turn(fetch_call(URLS[1]))
        assert kwargs["tools"], "Below the limit the model keeps its tools"
        return turn(content="Значения 10 и 20.")

    events = run(tmp_path, chat)
    notes = [text for text in tool_texts(calls[2]["messages"]) if "Прочитано сайтов" in text]
    assert len(notes) == 1 and "Прочитано сайтов: 2 из 5." in notes[0]
    assert "Если прочитанного хватает для ответа" in notes[0]
    assert final(events)["text"] == "Значения 10 и 20." and reads == URLS[:2]


def test_fifth_site_stops_reading_and_the_next_turn_answers_without_tools(tmp_path, reads):
    calls = []

    def chat(**kwargs):
        calls.append(snapshot(kwargs))
        if len(calls) == 1:
            return turn(fetch_call(*URLS[:6]))
        assert kwargs["tools"] == [] and ANSWER_TURN in kwargs["messages"][-1]["content"]
        assert "лимит 5" in kwargs["messages"][-1]["content"]
        return turn(content="По пяти прочитанным страницам значения 10–50; шестую не читал.")

    events = run(tmp_path, chat)
    assert len(calls) == 2 and reads == URLS[:5]
    note = tool_texts(calls[1]["messages"])[-1]
    assert URLS[5] in note and "не прочитаны" in note
    assert "Прочитано сайтов: 5 — лимит 5 на вопрос." in note
    assert final(events)["text"].startswith("По пяти прочитанным") and events[-1]["stop_reason"] == "answer"


def test_parallel_reads_never_go_past_the_limit(tmp_path, reads):
    calls = []

    def chat(**kwargs):
        calls.append(snapshot(kwargs))
        if len(calls) == 1:
            return turn(fetch_call(*URLS[:4]))
        if len(calls) == 2:
            return turn(fetch_call(URLS[4], call_id="a"), fetch_call(URLS[5], call_id="b"))
        assert kwargs["tools"] == []
        return turn(content="Ответ по пяти страницам.")

    events = run(tmp_path, chat)
    rejected = [event for event in events if event["type"] == "tool_call" and not event["ok"]]
    assert reads == URLS[:5]
    assert [event["error"] for event in rejected] == ["web_site_limit"]
    assert final(events)["text"] == "Ответ по пяти страницам."


def test_explicit_deep_analysis_allows_ten_sites(tmp_path, reads):
    calls = []

    def chat(**kwargs):
        calls.append(snapshot(kwargs))
        if len(calls) == 1:
            return turn(fetch_call(*URLS[:5]))
        if len(calls) == 2:
            assert kwargs["tools"], "Deep analysis keeps reading past five"
            assert "Прочитано сайтов: 5 из 10." in tool_texts(kwargs["messages"])[-1]
            return turn(fetch_call(URLS[5], URLS[6]))
        return turn(content="Глубокий разбор по семи страницам.")

    events = run(tmp_path, chat, message="Сделай глубокий анализ: что означают значения на страницах?")
    assert reads == URLS[:7] and final(events)["text"] == "Глубокий разбор по семи страницам."


@pytest.mark.parametrize(("request_text", "limit"), [
    ("Найди цену на ноутбук.", 5),
    ("Сделай глубокий анализ рынка ноутбуков.", 10),
    ("Нужен подробный обзор мнений.", 10),
    ("Do a deep research on this topic.", 10),
    ("Прочитай 8 сайтов и сравни.", 8),
    ("Сравни 20 источников.", 10),
    ("Прочитай 3 сайта.", 5),
    ("Глубокий анализ не нужен, просто найди адрес.", 5),
    ("Без подробного анализа: какая погода?", 5),
    ("Пример запроса: `глубокий анализ`. Найди погоду.", 5),
])
def test_site_limit_is_raised_only_by_an_explicit_user_request(request_text, limit):
    assert explicit_web_site_limit(request_text) == limit
