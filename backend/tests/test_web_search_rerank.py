"""Search results ordered by the local reranker (John 2026-10-13).

Only the SearXNG facade and the reranker HTTP call are stubbed; result selection,
receipts and presentation run their production code.
"""
from unittest.mock import patch

import pytest

from webskill import context
from webskill.application.code_agent.tools import _web as skill_web
from webskill.application.code_agent.tools._web import tool_web_search

QUESTION = "Найди официальные бюллетени безопасности NGINX"
RU, EN = "бюллетени безопасности nginx", "nginx security advisories"


def _rows(prefix, count):
    return [{"href": f"https://{prefix}.example/{index}", "title": f"{prefix} {index}", "body": f"about {prefix} {index}"}
            for index in range(count)]


def _search(asked):
    def search(query, **kwargs):
        asked.append((query, kwargs["max_results"]))
        prefix = "ru" if query == RU else "en"
        return {"ok": True, "sources": _rows(prefix, kwargs["max_results"])}
    return search


def _run(asked, **kwargs):
    with patch("webskill.infrastructure.search.web_search.search_web", side_effect=_search(asked)):
        return tool_web_search(queries=[RU, EN], top_k=5, **kwargs)


def test_without_the_reranker_the_engine_order_is_kept(monkeypatch):
    monkeypatch.delenv("LOCAL_RERANK_ENABLED", raising=False)
    asked = []
    result = _run(asked, question=QUESTION)
    assert sorted(asked) == [(EN, 5), (RU, 5)]
    urls = [source["url"] for source in result["sources"]]
    assert urls == [f"https://ru.example/{i}" for i in range(5)] + [f"https://en.example/{i}" for i in range(5)]
    assert result["text"].startswith("Found 10 results across 2 parallel queries")


def test_the_reranker_orders_all_results_by_the_users_question(monkeypatch):
    monkeypatch.setenv("LOCAL_RERANK_ENABLED", "true")
    seen = {}

    def scores(question, documents):
        seen.update(question=question, count=len(documents))
        # The 4th English result is the best, then the 2nd Russian one.
        return [10.0 if "en.example/3" in doc else 9.0 if "ru.example/1" in doc else 0.0 for doc in documents]

    monkeypatch.setattr(context, "rerank_scores", scores)
    asked = []
    result = _run(asked, question=QUESTION)
    assert sorted(asked) == [(EN, 10), (RU, 10)]
    assert seen == {"question": QUESTION, "count": 16}  # 8 of each, in turn
    urls = [source["url"] for source in result["sources"]]
    assert urls[:2] == ["https://en.example/3", "https://ru.example/1"] and len(urls) == 8
    assert result["text"].startswith("Best 8 of 20 results across 2 parallel queries")
    shown = {source_id for row in result["query_sources"] for source_id in row["source_ids"]}
    assert shown == {source["id"] for source in result["sources"]}


def test_a_failed_reranker_falls_back_to_the_previous_selection(monkeypatch):
    monkeypatch.setenv("LOCAL_RERANK_ENABLED", "true")
    monkeypatch.setattr(context, "rerank_scores", lambda question, documents: None)
    result = _run([], question=QUESTION)
    urls = [source["url"] for source in result["sources"]]
    assert urls == [f"https://ru.example/{i}" for i in range(5)] + [f"https://en.example/{i}" for i in range(5)]
    assert result["text"].startswith("Found 10 results")


def test_without_a_question_the_queries_are_the_reranker_input(monkeypatch):
    monkeypatch.setenv("LOCAL_RERANK_ENABLED", "true")
    seen = []
    monkeypatch.setattr(context, "rerank_scores",
                        lambda question, documents: seen.append(question) or [0.0] * len(documents))
    _run([])
    assert seen == [f"{RU} {EN}"]


def test_a_single_query_keeps_no_more_than_it_asked_for(monkeypatch):
    monkeypatch.setenv("LOCAL_RERANK_ENABLED", "true")
    monkeypatch.setattr(context, "rerank_scores",
                        lambda question, documents: [float(index) for index in range(len(documents))])
    asked = []
    with patch("webskill.infrastructure.search.web_search.search_web", side_effect=_search(asked)):
        result = tool_web_search(query=EN, top_k=3, question=QUESTION)
    assert asked == [(EN, 10)]
    assert [source["url"] for source in result["sources"]] == [
        "https://en.example/9", "https://en.example/8", "https://en.example/7"]


def test_images_are_never_reranked(monkeypatch):
    monkeypatch.setenv("LOCAL_RERANK_ENABLED", "true")
    monkeypatch.setattr(context, "rerank_scores", lambda *args: pytest.fail("images are not reranked"))
    asked = []
    with patch("webskill.infrastructure.search.web_search.search_web", side_effect=_search(asked)):
        tool_web_search(query=EN, top_k=5, categories="images", question=QUESTION)
    assert asked == [(EN, 5)]


def test_the_rerank_client_reads_scores_by_index_and_stays_local(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"index": 1, "relevance_score": 2.5}, {"index": 0, "relevance_score": -1.0}]}

    sent = {}
    monkeypatch.setenv("LOCAL_RERANK_ENABLED", "true")
    monkeypatch.setattr("requests.post", lambda url, **kwargs: sent.update(url=url, **kwargs) or Response())
    assert context.rerank_scores("вопрос", ["a", "b"]) == [-1.0, 2.5]
    assert sent["url"] == "http://192.168.88.15:8007/v1/rerank" and sent["timeout"] == 4.0
    monkeypatch.setenv("LOCAL_RERANK_ENABLED", "false")
    assert context.rerank_scores("вопрос", ["a", "b"]) is None
    monkeypatch.setenv("LOCAL_RERANK_BASE_URL", "https://api.example.com/v1")
    with pytest.raises(ValueError):
        context.rerank_scores("вопрос", ["a"])


def test_the_users_question_reaches_the_skill(tmp_path, monkeypatch):
    from app.application.code_agent import agent_loop
    from app.application.code_agent.tools import _web as backend_web

    calls = []
    monkeypatch.setattr(backend_web, "_call", lambda root, action, options: calls.append((action, options))
                        or {"ok": True, "text": "No web results.", "sources": []})
    replies = iter([
        {"message": {"content": "", "tool_calls": [{"function": {"name": "web_search", "arguments": {
            "queries": ["nginx security advisories"], "audience": "global", "mode": "quick"}}}]}},
        {"message": {"content": "Ничего не нашла.", "tool_calls": []}},
    ])
    list(agent_loop.stream_code_agent(
        user_message=QUESTION, project_root=tmp_path, chat_fn=lambda **kwargs: next(replies),
        base_tools=["web_search"], auto_remember=False, permission_mode="bypass",
    ))
    action, options = calls[0]
    assert action == "search" and options[options.index("--question") + 1] == QUESTION


def test_home_and_section_pages_go_after_the_articles(monkeypatch):
    # KZ run dbd2b141: "Новости Казахстана" home pages scored highest and were read five times.
    monkeypatch.setenv("LOCAL_RERANK_ENABLED", "true")
    rows = [{"href": "https://www.nur.kz/", "title": "Новости Казахстана", "body": "последние новости"},
            {"href": "https://www.zakon.kz/news", "title": "Новости Казахстана", "body": "рубрика"},
            {"href": "https://www.nur.kz/incident/2441218-ubiystvo-v-almaty/", "title": "Убийство в Алматы", "body": "ЧП"},
            {"href": "https://www.zakon.kz/6534652-vybory.html", "title": "Выборы в маслихаты", "body": "политика"}]
    monkeypatch.setattr(context, "rerank_scores", lambda question, documents: [9.0, 8.0, 2.0, 1.0])
    with patch("webskill.infrastructure.search.web_search.search_web",
               side_effect=lambda query, **kwargs: {"ok": True, "sources": rows}):
        result = tool_web_search(query="новости Казахстана за неделю", top_k=5, question="Новости Казахстана за неделю")
    assert [source["url"] for source in result["sources"]] == [
        "https://www.nur.kz/incident/2441218-ubiystvo-v-almaty/", "https://www.zakon.kz/6534652-vybory.html",
        "https://www.nur.kz/", "https://www.zakon.kz/news"]
