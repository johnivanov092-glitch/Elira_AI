"""John 2026-10-06 (industry practice): the model cites read pages by number; Elira renders the links."""
from copy import deepcopy

import pytest

from app.application.code_agent import agent_loop
from app.application.code_agent.answer_contracts import render_numbered_citations
from app.application.code_agent.tools import _web
from app.infrastructure.search.web_runtime import PageFetchResult

PAGES = [("https://tengrinews.kz/health/virus", "TengriNews"), ("https://www.lada.kz/news/1", "Lada")]


@pytest.mark.parametrize(("text", "expected"), [
    ("Факт [1].", "Факт [tengrinews.kz](https://tengrinews.kz/health/virus)."),
    ("Источники: [TengriNews / НЦОЗ][1], [Lada][2].",
     "Источники: [TengriNews / НЦОЗ](https://tengrinews.kz/health/virus), [Lada](https://www.lada.kz/news/1)."),
    ("Оба [1][2].", "Оба [tengrinews.kz](https://tengrinews.kz/health/virus), [lada.kz](https://www.lada.kz/news/1)."),
    ("Оба [1, 2].", "Оба [tengrinews.kz](https://tengrinews.kz/health/virus), [lada.kz](https://www.lada.kz/news/1)."),
    ("Нет [7] и [Подпись][9].", "Нет и Подпись."),  # unknown numbers: no invented address
    ("Год [2026] и готовая [ссылка](https://example.org).", "Год [2026] и готовая [ссылка](https://example.org)."),
])
def test_numbers_become_links_to_read_pages_only(text, expected):
    assert render_numbered_citations(text, PAGES) == expected


def test_code_is_never_touched_and_rendering_is_idempotent():
    text = "Код `x = [1]`:\n```python\nitems[2]\n```\nИсточник [2]."
    once = render_numbered_citations(text, PAGES)
    assert "`x = [1]`" in once and "items[2]" in once
    assert once.endswith("Источник [lada.kz](https://www.lada.kz/news/1).")
    assert render_numbered_citations(once, PAGES) == once
    assert render_numbered_citations("Факт [1].", []) == "Факт [1]."  # nothing read: nothing to render


def test_read_page_gets_a_number_and_the_answer_gets_the_link(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr("app.infrastructure.llm.openai_compatible.server_context_window",
                        lambda *, fresh=True: 65536)
    url = "https://example.org/report"
    monkeypatch.setattr(_web, "_fetch_one", lambda u, limit, **_: PageFetchResult(
        text="В отчёте указано значение 42 для показателя.", final_url=u, status_code=200))
    captures = []

    def chat(**kwargs):
        captures.append(deepcopy(kwargs["messages"]))
        if len(captures) == 1:
            return {"message": {"content": "", "tool_calls": [{
                "id": "read-1", "function": {"name": "web_fetch", "arguments": {"url": url}}}]}}
        return {"message": {"content": "Значение — 42 [Отчёт][1]; ещё [5].", "tool_calls": []}}

    events = list(agent_loop.stream_code_agent(
        user_message="Какое значение в отчёте?", project_root=tmp_path, num_ctx=65536,
        base_tools=["web_fetch", "web_search"], permission_mode="bypass", auto_remember=False, chat_fn=chat))
    tool_text = next(m["content"] for m in captures[1] if m.get("role") == "tool")
    assert f"[1] {url}" in tool_text and "адрес сам не пиши" in tool_text
    final = next(event for event in events if event["type"] == "final_response")
    assert final["text"] == f"Значение — 42 [Отчёт]({url}); ещё."
