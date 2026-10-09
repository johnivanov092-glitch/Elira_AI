"""John's web path: the model answers in prose; 2 sites → enough? : up to 10 (deep 20) → answer."""

from copy import deepcopy
import re

import pytest

from _runtime_roles import runtime_text
from app.application.code_agent import agent_loop
from webskill.application.code_agent.answer_contracts import explicit_web_site_limit
from webskill.application.code_agent.tools import _web
from webskill.infrastructure.search.web_runtime import PageFetchResult


URLS = [f"https://example.org/page{index}" for index in range(1, 24)]
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












@pytest.mark.parametrize(("request_text", "limit"), [
    ("Найди цену на ноутбук.", 10),
    ("Сделай глубокий анализ рынка ноутбуков.", 20),
    ("Нужен подробный обзор мнений.", 20),
    ("Do a deep research on this topic.", 20),
    ("Прочитай 8 сайтов и сравни.", 10),
    ("Прочитай 15 сайтов и сравни.", 15),
    ("Сравни 30 источников.", 20),
    ("Прочитай 3 сайта.", 10),
    ("Глубокий анализ не нужен, просто найди адрес.", 10),
    ("Без подробного анализа: какая погода?", 10),
    ("Пример запроса: `глубокий анализ`. Найди погоду.", 10),
])
def test_site_limit_is_raised_only_by_an_explicit_user_request(request_text, limit):
    assert explicit_web_site_limit(request_text) == limit
