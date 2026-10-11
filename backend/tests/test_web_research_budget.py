"""Research rules at the skill boundary; real page cache avoids duplicate I/O."""
import threading
import json
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from webskill import context, research
from webskill.infrastructure.search.web_runtime import PageFetchResult


def args(*, mode=None, urls=None, step=1, find="", store=False,
         queries=None, audience="global"):
    return SimpleNamespace(mode=mode, url=urls or [], step=step, find=find, store=store,
                           query=queries if queries is not None else ["English query"],
                           audience=audience)


@pytest.mark.parametrize("queries", [["English query"], ["Русский запрос"]])
def test_quick_has_one_single_language_search_and_then_closes_search(queries):
    operation = Mock(return_value={"ok": True, "text": "found", "sources": [{"url": "https://a"}]})
    first = research.execute("search", args(mode="quick", queries=queries), operation)
    assert first["ok"] and "error" not in first and not first.get("partial")
    assert first["sources"] == [{"url": "https://a"}] and first["text"] == "found"
    assert first["web_research"]["search_rounds"] == 1
    assert "language_correction_available" not in first["web_research"]
    second = research.execute("search", args(queries=["Another query"]), operation)
    assert second["error"] == "web_round_budget" and operation.call_count == 1


def test_quick_mode_cannot_be_promoted_by_later_search_or_fetch():
    operation = Mock(return_value={"ok": True, "text": "found"})
    first = research.execute("search", args(mode="quick", queries=["English query"]), operation)
    assert first["ok"]
    second = research.execute("search", args(mode="deep"), operation)
    assert second["error"] == "web_round_budget" and second["web_research"]["mode"] == "quick"
    assert second["web_research"]["search_rounds"] == 1
    research.reset()  # The effective mode must survive subsequent skill processes.
    four_pages = research.execute("fetch", args(mode="deep", urls=[f"https://a/{i}" for i in range(4)]), operation)
    assert four_pages["error"] == "web_batch_budget" and four_pages["web_research"]["mode"] == "quick"
    third = research.execute("search", args(mode="deep"), operation)
    assert third["error"] == "web_round_budget" and operation.call_count == 1


def test_first_quick_fetch_cannot_be_promoted_by_a_later_search():
    research.execute("fetch", args(urls=["https://a"]), lambda: {
        "ok": True, "pages": [{"url": "https://a", "ok": True}]})
    operation = Mock(return_value={"ok": True, "text": "found"})
    search = research.execute("search", args(mode="deep"), operation)
    assert search["ok"] and search["web_research"]["mode"] == "quick"
    assert research.execute("search", args(mode="deep"), operation)["error"] == "web_round_budget"
    stopped = research.execute("fetch", args(mode="deep", urls=["https://b"]), operation)
    assert stopped["error"] == "web_site_budget" and operation.call_count == 1


def test_later_quick_does_not_shrink_the_first_deep_fetch_budget():
    urls = [f"https://a/{i}" for i in range(8)]
    first = research.execute("fetch", args(mode="deep", urls=urls), lambda: {
        "ok": True, "pages": [{"url": url, "ok": True} for url in urls]})
    assert first["ok"] and first["web_research"]["mode"] == "deep"
    research.reset()
    second_urls = [f"https://b/{i}" for i in range(8)]
    second = research.execute("fetch", args(mode="quick", urls=second_urls), lambda: {
        "ok": True, "pages": [{"url": url, "ok": True} for url in second_urls]})
    assert second["ok"] and second["web_research"]["mode"] == "deep"
    assert second["web_research"]["read_rounds"] == 2 and second["web_research"]["sites"] == 16


def test_deep_single_language_search_has_only_three_rounds():
    operation = Mock(return_value={"ok": True, "text": "found", "sources": [{"url": "https://a"}]})
    for round_number in range(1, 4):
        result = research.execute("search", args(mode="deep", queries=["English query"]), operation)
        assert result["ok"] and "error" not in result and not result.get("partial")
        assert result["sources"] == [{"url": "https://a"}] and result["text"].endswith("found")
        assert result["web_research"]["search_rounds"] == round_number
        assert "language_correction_available" not in result["web_research"]
    stopped = research.execute("search", args(mode="deep"), operation)
    assert stopped["error"] == "web_round_budget" and operation.call_count == 3


def test_research_preserves_transport_failure():
    result = research.execute("search", args(queries=["English query"]), lambda: {
        "ok": False, "partial": True, "error": "search_unavailable", "text": "Transport failed.",
        "sources": [{"url": "https://a"}]})
    assert result["error"] == "search_unavailable" and not result["ok"] and result["partial"]
    assert result["text"].endswith("Transport failed.") and result["sources"] == [{"url": "https://a"}]


@pytest.mark.parametrize("queries,audience", [
    (["English query"], "global"),
    (["Новости Казахстана"], "regional:Kazakhstan"),
])
def test_quick_has_no_extra_search_for_global_or_regional(queries, audience):
    operation = Mock(return_value={"ok": True, "text": "found"})
    assert research.execute("search", args(queries=queries, audience=audience), operation)["ok"]
    result = research.execute("search", args(queries=["Another query"]), operation)
    assert result["error"] == "web_round_budget" and operation.call_count == 1


@pytest.mark.parametrize("searches", [1, 2])
@pytest.mark.parametrize("pending", [False, True])
def test_resume_drops_legacy_correction_without_reopening_quota(searches, pending, monkeypatch):
    monkeypatch.setattr(research.time, "time", lambda: 125.0)
    state = {"started_at": 100.0, "mode": "quick", "searches": searches, "fetches": 1,
             "urls": ["https://a"], "read_urls": [], "failed_urls": ["https://a"],
             "language_correction": pending}
    path = context.data_file("research.json")
    research._write(path, state)
    operation = Mock(return_value={"ok": True, "text": "read", "pages": [{"url": "https://b", "ok": True}]})
    resumed = research.execute("fetch", args(urls=["https://b"]), operation)
    assert resumed["ok"] and resumed["web_research"]["elapsed_seconds"] == 25
    assert resumed["web_research"]["search_rounds"] == searches
    assert resumed["web_research"]["read_rounds"] == 2 and resumed["web_research"]["sites"] == 1
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert "language_correction" not in stored and "language_correction_available" not in resumed["web_research"]
    assert stored["started_at"] == 100.0 and stored["mode"] == "quick" and stored["searches"] == searches
    assert stored["urls"] == ["https://a", "https://b"] and stored["failed_urls"] == ["https://a"]
    assert stored["read_urls"] == ["https://b"]
    stopped = research.execute("search", args(mode="deep"), operation)
    assert stopped["error"] == "web_round_budget" and operation.call_count == 1


def test_single_language_search_preserves_global_deadline_and_three_page_quota(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(research.time, "time", lambda: now[0])
    operation = Mock(return_value={"ok": True, "text": "found"})
    research.execute("search", args(queries=["English query"]), operation)
    assert research.execute("search", args(queries=["Another query"]), operation)["error"] == "web_round_budget"
    result = research.execute("fetch", args(urls=[f"https://a/{i}" for i in range(4)]), operation)
    assert result["error"] == "web_batch_budget" and operation.call_count == 1
    now[0] = 160.0
    result = research.execute("fetch", args(urls=["https://a"]), operation)
    assert result["error"] == "web_time_budget" and operation.call_count == 1


def test_warning_and_stop_without_dispatch(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(research.time, "time", lambda: now[0])
    operation = Mock(return_value={"ok": True, "text": "collected"})
    research.execute("search", args(mode="deep"), operation)
    now[0] = 131.0
    result = research.execute("search", args(mode="deep"), operation)
    assert result["ok"] and "Прошло 30 с" in result["text"]
    now[0] = 160.0
    result = research.execute("search", args(mode="deep"), operation)
    assert result["error"] == "web_time_budget" and result["web_research"]["stopped"]
    assert operation.call_count == 2


@pytest.mark.parametrize("elapsed,warning", [(31, "Прошло 30 с"), (61, "Лимит времени исследования")])
def test_time_budget_notice_precedes_long_page_body_for_event_preview(monkeypatch, elapsed, warning):
    now = [100.0]
    monkeypatch.setattr(research.time, "time", lambda: now[0])

    def operation():
        now[0] += elapsed
        return {"ok": True, "text": "Source body. " * 1000}

    result = research.execute("search", args(mode="deep"), operation)
    assert len(result["text"]) > 4000
    assert result["text"][:4000].startswith(warning)


def test_quick_can_replace_failed_source_once():
    first = research.execute("fetch", args(urls=["https://a", "https://b"]), lambda: {
        "ok": True, "text": "one read", "pages": [
            {"url": "https://a", "ok": True}, {"url": "https://b", "ok": False}]})
    assert first["web_research"]["sites"] == 1
    replacement = research.execute("fetch", args(urls=["https://c", "https://d"]), lambda: {
        "ok": True, "text": "two read", "pages": [
            {"url": "https://c", "ok": True}, {"url": "https://d", "ok": True}]})
    assert replacement["ok"] and replacement["web_research"]["sites"] == 3
    operation = Mock()
    stopped = research.execute("fetch", args(urls=["https://e"]), operation)
    assert stopped["error"] == "web_site_budget"
    operation.assert_not_called()


def test_quick_does_not_expand_after_success():
    research.execute("fetch", args(urls=["https://a"]), lambda: {
        "ok": True, "text": "read", "pages": [{"url": "https://a", "ok": True}]})
    operation = Mock()
    result = research.execute("fetch", args(urls=["https://b"]), operation)
    assert result["error"] == "web_site_budget"
    operation.assert_not_called()


def test_missing_find_on_read_page_does_not_allow_quick_replacement(monkeypatch):
    from webskill.application.code_agent.tools import _web

    url = "https://example.org/read"
    fetch = Mock(return_value=PageFetchResult(text="Official page text. " * 30, final_url=url))
    monkeypatch.setattr(_web, "_fetch_one_live", fetch)
    result = research.execute("fetch", args(urls=[url]), lambda: _web.tool_web_fetch(url=url))
    assert result["ok"] and result["web_research"]["sites"] == 1
    missing = research.execute("fetch", args(urls=[url]),
                               lambda: _web.tool_web_fetch(url=url, find="absent unique phrase"))
    assert not missing["ok"]
    assert missing["pages"][0]["error"].startswith("find phrase not found")
    assert fetch.call_count == 1
    operation = Mock()
    stopped = research.execute("fetch", args(urls=["https://example.org/new"]), operation)
    assert stopped["error"] == "web_site_budget"
    operation.assert_not_called()


def test_missing_find_on_first_fetch_counts_extracted_page_as_read(monkeypatch):
    from webskill.application.code_agent.tools import _web

    url = "https://example.org/read"
    fetch = Mock(return_value=PageFetchResult(text="Official page text. " * 30, final_url=url))
    monkeypatch.setattr(_web, "_fetch_one_live", fetch)
    result = research.execute("fetch", args(urls=[url], find="absent unique phrase"),
                              lambda: _web.tool_web_fetch(url=url, find="absent unique phrase"))
    assert not result["ok"] and result["web_research"]["sites"] == 1
    state = json.loads(context.data_file("research.json").read_text(encoding="utf-8"))
    assert state["read_urls"] == [url] and state["failed_urls"] == []
    operation = Mock()
    stopped = research.execute("fetch", args(urls=["https://example.org/new"]), operation)
    assert stopped["error"] == "web_site_budget"
    operation.assert_not_called()
    assert fetch.call_count == 1


@pytest.mark.parametrize("bad", [
    args(urls=["https://a"], find="phrase", store=True),
    args(urls=["https://a"], find="x" * 201),
    args(urls=["https://a"], find=1),
    args(urls=[123]), args(),
])
def test_invalid_fetch_arguments_do_not_consume_research_budget(bad):
    operation = Mock()
    result = research.execute("fetch", bad, operation)
    assert result["error"] == "argument_format"
    operation.assert_not_called()
    assert not context.data_file("research.json").exists()
    good = research.execute("fetch", args(urls=["https://a"]), lambda: {
        "ok": True, "pages": [{"url": "https://a", "ok": True}]})
    assert good["ok"] and good["web_research"]["read_rounds"] == 1
    state_file = context.data_file("research.json")
    before = state_file.read_bytes()
    assert research.execute("fetch", bad, operation)["error"] == "argument_format"
    assert state_file.read_bytes() == before


def test_fragment_views_have_separate_cache_but_share_quota_and_queries_do_not(monkeypatch):
    from webskill.application.code_agent.tools import _web

    base = "https://example.org/document?q=first"
    fetch = Mock(side_effect=lambda url, limit, **kwargs:
                 PageFetchResult(text=f"Full document from {url}. " * 30, final_url=url))
    monkeypatch.setattr(_web, "_fetch_one_live", fetch)
    result = research.execute("fetch", args(urls=[base + "#first", base + "#second"]),
                              lambda: _web.tool_web_fetch(urls=[base + "#first", base + "#second"]))
    assert result["ok"] and result["web_research"]["sites"] == 1
    assert fetch.call_count == 2
    assert {call.args[0] for call in fetch.call_args_list} == {base + "#first", base + "#second"}
    result = research.execute("fetch", args(urls=[base + "#first"]),
                              lambda: _web.tool_web_fetch(url=base + "#first"))
    assert result["ok"] and fetch.call_count == 2
    result = research.execute("fetch", args(urls=[base]), lambda: _web.tool_web_fetch(url=base))
    assert result["ok"] and fetch.call_count == 3
    operation = Mock()
    stopped = research.execute("fetch", args(urls=[base.replace("first", "second")]), operation)
    assert stopped["error"] == "web_site_budget"
    operation.assert_not_called()
    state = json.loads(context.data_file("research.json").read_text(encoding="utf-8"))
    assert state["urls"] == [base] and state["read_urls"] == [base]


def test_research_anchor_cache_preserves_get_and_head_sections(monkeypatch):
    from webskill.application.code_agent.tools import _web

    base = "https://example.org/rfc"
    html = ("<main><h1>HTTP semantics</h1><p>Introduction. " + "Unrelated material. " * 40
            + "</p><h2 id='name-get'>GET</h2><p>GET retrieves a representation. " * 20
            + "</p><h2 id='name-head'>HEAD</h2><p>HEAD omits response content. " * 20
            + "</p></main>")
    def response(*a, **kw):
        stream = io.BytesIO(html.encode("utf-8"))
        raw = SimpleNamespace(read1=lambda amt, **kw: stream.read1(amt),
                              connection=SimpleNamespace(sock=SimpleNamespace(shutdown=Mock())))
        return SimpleNamespace(text=html, status_code=200, url=base, encoding="utf-8",
                               headers={"Content-Type": "text/html"}, close=Mock(), raw=raw)
    request = Mock(side_effect=response)
    monkeypatch.setattr("webskill.infrastructure.search.web_runtime._get_page_response", request)
    for fragment, present, absent in [
        ("name-get", "GET retrieves a representation", "HEAD omits response content"),
        ("name-head", "HEAD omits response content", "GET retrieves a representation"),
        ("name-get", "GET retrieves a representation", "HEAD omits response content"),
    ]:
        url = base + "#" + fragment
        result = research.execute("fetch", args(urls=[url]), lambda: _web.tool_web_fetch(url=url))
        assert result["ok"] and present in result["text"] and absent not in result["text"]
        assert "Unrelated material" not in result["text"]
        assert result["pages"][0]["fragment_found"] is True
        assert result["web_research"]["read_rounds"] == 1
        assert result["web_research"]["sites"] == 1
    assert request.call_count == 2


def test_deep_rounds_and_parallel_batch_limits():
    operation = Mock(return_value={"ok": True, "text": "found"})
    for _ in range(3):
        assert research.execute("search", args(mode="deep"), operation)["ok"]
    assert research.execute("search", args(mode="deep"), operation)["error"] == "web_round_budget"
    assert operation.call_count == 3
    result = research.execute("fetch", args(urls=[f"https://a/{i}" for i in range(9)]), operation)
    assert result["error"] == "web_batch_budget" and operation.call_count == 3


def test_cli_fetch_can_declare_deep_without_a_search_and_then_inherit_it():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "skills/web-research/web.py"
    spec = importlib.util.spec_from_file_location("web_research_cli_for_budget_test", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    urls = [f"https://example.org/{i}" for i in range(8)]
    command = ["fetch", "--mode", "deep", *[part for url in urls for part in ("--url", url)]]
    first = cli.parser().parse_args(command)
    result = research.execute("fetch", first, lambda: {
        "ok": True, "pages": [{"url": url, "ok": True} for url in urls]})
    assert result["ok"] and result["web_research"]["mode"] == "deep"
    assert result["web_research"]["search_rounds"] == 0
    inherited = cli.parser().parse_args(["fetch", "--url", "https://example.org/next"])
    assert inherited.mode is None
    result = research.execute("fetch", inherited, lambda: {
        "ok": True, "pages": [{"url": "https://example.org/next", "ok": True}]})
    assert result["ok"] and result["web_research"]["mode"] == "deep"


def test_deep_site_budget_counts_failed_url_attempts():
    for batch in range(2):
        urls = [f"https://example.org/{batch}/{i}" for i in range(8)]
        result = research.execute("fetch", args(mode="deep", urls=urls), lambda: {
            "ok": False, "text": "unavailable", "pages": [{"url": url, "ok": False} for url in urls]})
        assert result["web_research"]["read_rounds"] == batch + 1
    operation = Mock()
    urls = [f"https://example.org/final/{i}" for i in range(8)]
    stopped = research.execute("fetch", args(urls=urls), operation)
    assert stopped["error"] == "web_site_budget"
    operation.assert_not_called()
    # The last round can still use the four remaining sites.
    result = research.execute("fetch", args(urls=urls[:4]), lambda: {
        "ok": True, "text": "read", "pages": [{"url": url, "ok": True} for url in urls[:4]]})
    assert result["ok"] and result["web_research"]["read_rounds"] == 3


def test_eight_page_batch_really_runs_concurrently_and_ninth_is_rejected(monkeypatch):
    from webskill.application.code_agent.tools import _web

    urls = [f"https://example.org/{i}" for i in range(8)]
    started = []
    barrier = threading.Barrier(8)
    lock = threading.Lock()

    def fetch(url, limit, **kwargs):
        with lock:
            started.append(url)
        # All eight must be in the fetch boundary together, rather than a
        # capped or serial operation accidentally passing a worker-count check.
        barrier.wait(timeout=5)
        return PageFetchResult(text=f"Official text for {url}. " * 20, final_url=url)

    monkeypatch.setattr(_web, "_fetch_one_live", fetch)
    result = research.execute("fetch", args(mode="deep", urls=urls),
                              lambda: _web.tool_web_fetch(urls=urls))
    assert result["ok"] and len(result["pages"]) == 8
    assert all(page["ok"] for page in result["pages"])
    assert sorted(started) == sorted(urls)
    assert len(result["sources"]) >= 8
    assert result["web_research"]["sites"] == 8
    operation = Mock()
    stopped = research.execute("fetch", args(urls=urls + ["https://example.org/ninth"]), operation)
    assert stopped["error"] == "web_batch_budget"
    operation.assert_not_called()
    assert len(started) == 8


@pytest.mark.parametrize("error", ["", "HTTP 404"])
def test_repeat_page_uses_cache_across_skill_process_state(error):
    fetch = Mock(return_value=PageFetchResult(text="official text " * 100, final_url="https://a",
                                             error=error, title="Official title"))
    def operation():
        page = research.cached_page("https://a", 8000, fetch)
        return {"ok": page.ok, "text": page.text or page.error,
                "pages": [{"url": "https://a", "ok": page.ok}]}
    research.execute("fetch", args(urls=["https://a"], step=4), operation)
    research.reset()  # A new CLI process loads the same persisted research state.
    result = research.execute("fetch", args(urls=["https://a"], step=8), operation)
    assert fetch.call_count == 1
    assert "уже прочитано на шаге 4" in result["text"]


def test_remaining_timeout_tracks_shared_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(research.time, "time", lambda: now[0])
    research.execute("search", args(mode="deep"), lambda: {"ok": True, "text": "found"})
    now[0] = 158.0
    assert research.remaining_timeout(20) == 2
    now[0] = 160.0
    with pytest.raises(TimeoutError):
        research.remaining_timeout()


def test_native_deadline_frame_is_available_before_the_operation(monkeypatch, capsys):
    monkeypatch.setattr(research.time, "time", lambda: 100.0)
    native = args(mode="deep")
    native.native = True
    def operation():
        assert capsys.readouterr().out == '{"web_deadline_epoch": 160.0}\n'
        return {"ok": True, "text": "read"}
    assert research.execute("search", native, operation)["ok"]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("selector", ["id='content'", "class='content'", "class='text-content'"])
def test_generic_content_section_cannot_hide_the_rest_of_a_technical_document(selector):
    from bs4 import BeautifulSoup
    from webskill.infrastructure.search.web_runtime import _extract_readable_text

    html = ("<body><section " + selector + "><h2>6.4. Content</h2><p>"
            + "Representation content describes framing. " * 8 + "</p></section>"
            + "<section id='section-9.3.2'><h2>9.3.2. HEAD</h2><p>"
            + "The HEAD method is identical to GET except that the server MUST NOT send content. " * 12
            + "</p></section></body>")
    text = _extract_readable_text(BeautifulSoup(html, "html.parser"), None)
    assert "6.4." in text and "9.3.2." in text and "MUST NOT send content" in text


@pytest.mark.parametrize("found,http_error", [
    ((True, True), False), ((True, False), False),
    ((False, False), False), ((True, False), True),
])
def test_batch_find_preserves_per_url_receipts_and_reuses_cache(monkeypatch, found, http_error):
    from webskill.application.code_agent.tools import _web
    from webskill.application.web_evidence.receipts import valid_source

    urls = ["https://example.org/a", "https://example.org/b"]
    texts = {url: "Introduction. " * 6000
             + ("Verified score: 80.1 percent.\n" if found[i] else "No score.\n")
             + "Appendix. " * 6000 for i, url in enumerate(urls)}

    def fetch(url, limit, **kwargs):
        if http_error and url == urls[1]:
            return PageFetchResult(final_url=url, status_code=404, error="HTTP 404")
        return PageFetchResult(text=texts[url], final_url=url, status_code=200)

    transport = Mock(side_effect=fetch)
    monkeypatch.setattr(_web, "_fetch_one_live", transport)
    for step in (1, 2):
        result = research.execute("fetch", args(urls=urls, step=step, find="Verified score"),
            lambda: _web.tool_web_fetch(urls=urls, find="Verified score", max_chars=50000))
        assert result["ok"] is any(found)
        assert [page["url"] for page in result["pages"]] == urls
        assert [page["ok"] for page in result["pages"]] == list(found)
        assert result["web_research"]["sites"] == 2 - int(http_error)
        assert result["web_research"]["read_rounds"] == 1
        assert transport.call_count == 2
        assert {source["url"] for source in result["sources"]} == set(urls)
        for source in result["sources"]:
            assert valid_source(source)
            if source["status"] == "excerpt":
                assert source["quote_verified"] and source["offset"] > 60000
                text, offset = texts[source["url"]], source["offset"]
                assert source["quote"] == text[offset:offset + len(source["quote"])]
            else:
                assert source["status"] == "failed"
                assert not source["quote_verified"] and not source["quote"]
        for i, page in enumerate(result["pages"]):
            if not found[i]:
                assert ("HTTP 404" if http_error else "find phrase not found") in page["error"]
        if step == 2:
            assert {row["url"] for row in result["web_research"]["cached"]} == set(urls)


def test_eight_url_find_and_cached_repeat_keep_12k_limit_and_all_receipts(monkeypatch):
    from webskill.application.code_agent.tools import _web
    from webskill.application.web_evidence.receipts import valid_source

    urls = [f"http://localhost/probe/eight_url_cap/{i}.txt" for i in range(8)]
    texts = {url: "Introduction unrelated to the result. " * 1800
             + f"Verified score: {80 + i}.1 percent.\n"
             + "Appendix continues with unneeded details. " * 1800
             for i, url in enumerate(urls)}
    transport = Mock(side_effect=lambda url, limit, **kwargs:
        PageFetchResult(text=texts[url], final_url=url, status_code=200, mime="text/plain"))
    monkeypatch.setattr(_web, "_fetch_one_live", transport)

    for step in (1, 2):
        result = research.execute("fetch", args(mode="deep", urls=urls, step=step, find="Verified score"),
            lambda: _web.tool_web_fetch(urls=urls, find="Verified score", max_chars=50000))
        assert result["ok"] and len(result["text"]) <= context.WEB_TOOL_RESULT_LLM_LIMIT
        assert [page["url"] for page in result["pages"]] == urls
        assert {source["url"] for source in result["sources"]} == set(urls)
        assert transport.call_count == 8 and result["web_research"]["read_rounds"] == 1
        for source in result["sources"]:
            assert valid_source(source) and source["quote_verified"]
            text, offset = texts[source["url"]], source["offset"]
            assert source["quote"] == text[offset:offset + len(source["quote"])]
            assert "Verified score" in source["quote"]
        if step == 2:
            assert len(result["web_research"]["cached"]) == 8
