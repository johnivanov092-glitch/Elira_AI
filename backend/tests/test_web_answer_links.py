"""Strict web contour (John 2026-10-06 and 2026-10-11): only real addresses open,
read pages are cited by number, links to unread pages lose their address, and a
home/section page is marked as headlines (KZ run 319d7f05, 11.10)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.application.agent_kernel.runtime_context import bind_runtime_context
from app.application.code_agent.answer_acceptance import AnswerAcceptance
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.task_outcomes import TaskOutcome
from app.application.code_agent.tools import _web

READ = "https://www.nur.kz/incident/crime/2441011-davka-na-koncerte/"
FAILED = "https://informburo.kz/novosti/pozar-v-astane"
DISCOVERED = "https://www.zakon.kz/sport/6534596-skonchalsya-vratar.html"
INVENTED = "https://www.zakon.kz/politics/6534578-korol-belgii-posetit-kazahstan.html"
SERVED = "https://www.zakon.kz/sovety/6534578-nevidimye-sosedi-v-kvartire.html"
HOME = "https://www.zakon.kz/"
ARTICLES = [{"label": f"Новость {n}", "url": f"https://www.zakon.kz/sobytiia/65346{n:02d}-novost.html"}
            for n in range(1, 7)]


def _acceptance(text: str, evidence: RunEvidence):
    return AnswerAcceptance().evaluate(
        final_text=text, raw_user_message="Новости Казахстана за неделю.",
        pending_redirected_jobs=(), active_capability_groups=set(), task_outcome=TaskOutcome(),
        run_evidence=evidence, code_input_epoch=0, quote_word_limit=None, step=3, run_id="kz-links")


def _evidence() -> RunEvidence:
    evidence = RunEvidence()
    evidence.record_tool_result(
        tool_name="web_search", arguments={"queries": ["новости Казахстана"]}, execution_status="ok",
        output={"ok": True, "sources": [{"url": DISCOVERED, "status": "discovered"},
                                         {"url": READ, "status": "discovered"}]},
        text_result="", state_changed=False)
    # One page failed in the same call: the read page must still count as read.
    evidence.record_tool_result(
        tool_name="web_fetch", arguments={"urls": [READ, FAILED]}, execution_status="ok",
        output={"ok": False, "sources": [{"url": READ, "status": "excerpt"},
                                          {"url": FAILED, "status": "failed", "error": "HTTP 404"},
                                          {"url": SERVED, "status": "excerpt"}]},
        text_result="", state_changed=False)
    return evidence


ANSWER = (
    f"Давка на концерте в Шымкенте, возбуждено уголовное дело. [NUR.KZ]({READ})\n\n"
    f"В Астане загорелась кабельная шахта, спасли троих. [Informburo]({FAILED})\n\n"
    f"Скончался легендарный вратарь «Кайрата». [Zakon]({DISCOVERED})\n\n"
    f"Король Бельгии посетит Казахстан 10 октября — подтверждено. [Zakon]({INVENTED})"
)


def test_points_resting_on_unread_pages_are_removed():
    decision = _acceptance(ANSWER, _evidence())
    assert decision.action == "accept" and decision.answer_status == "degraded"
    assert f"Давка на концерте в Шымкенте, возбуждено уголовное дело. [NUR.KZ]({READ})" in decision.text
    for gone in ("кабельная шахта", "вратарь", "Король Бельгии", FAILED, DISCOVERED, INVENTED):
        assert gone not in decision.text
    assert decision.text.endswith("_Убрано пунктов: 3 — не нашлось в прочитанных статьях или вне запрошенного периода._")


def test_answer_without_web_research_is_untouched():
    text = "Документация: [docs](https://docs.python.org/3/)"
    decision = AnswerAcceptance().evaluate(
        final_text=text, raw_user_message="Где документация Python?",
        pending_redirected_jobs=(), active_capability_groups=set(), task_outcome=TaskOutcome(),
        run_evidence=RunEvidence(), code_input_epoch=0, quote_word_limit=None, step=1, run_id="no-web")
    assert decision.text == text and decision.answer_status == "complete"


def test_numbers_become_links_and_a_home_page_source_gets_one_correction():
    evidence = RunEvidence()
    evidence.record_tool_result(
        tool_name="web_fetch", arguments={"urls": [READ, HOME]}, execution_status="ok",
        output={"ok": True, "page_numbers": {READ: 1}, "index_pages": [HOME],
                "pages": [{"url": READ, "final_url": READ, "title": "Давка на концерте", "ok": True},
                          {"url": HOME, "final_url": HOME, "title": "Zakon.kz", "ok": True}],
                "sources": [{"url": READ, "status": "excerpt"}, {"url": HOME, "status": "excerpt"}]},
        text_result="", state_changed=False)
    owner = AnswerAcceptance()
    answer = f"Давка в Шымкенте [1]. Скончался вратарь «Кайрата». [Zakon]({HOME})"
    first = owner.evaluate(
        final_text=answer, raw_user_message="Новости Казахстана за неделю.", pending_redirected_jobs=(),
        active_capability_groups=set(), task_outcome=TaskOutcome(), run_evidence=evidence,
        code_input_epoch=0, quote_word_limit=None, step=4, run_id="kz-home")
    assert first.action == "retry" and first.reason == "web_source" and HOME in first.correction
    owner.commit(first)
    second = owner.evaluate(
        final_text=answer, raw_user_message="Новости Казахстана за неделю.", pending_redirected_jobs=(),
        active_capability_groups=set(), task_outcome=TaskOutcome(), run_evidence=evidence,
        code_input_epoch=0, quote_word_limit=None, step=5, run_id="kz-home")
    assert second.action == "accept" and second.answer_status == "degraded"
    assert f"[nur.kz]({READ})" in second.text and HOME not in second.text


@pytest.fixture
def fake_skill(monkeypatch):
    calls: list[tuple[str, list[str]]] = []
    responses: dict[str, dict] = {}

    def call(project_root, action, options):
        urls = [options[i + 1] for i, part in enumerate(options) if part == "--url"]
        calls.append((action, urls))
        return responses[action](urls) if callable(responses.get(action)) else responses[action]
    monkeypatch.setattr(_web, "_call", call)
    return calls, responses


def _page(url, final=None, title="Страница", links=()):
    return {"url": url, "final_url": final or url, "title": title, "ok": True, "links": list(links)}


def test_invented_address_is_refused_without_a_request(tmp_path: Path, fake_skill):
    calls, _ = fake_skill
    result = _web.tool_web_fetch(tmp_path, urls=[INVENTED])
    assert result["ok"] is False and result["error"] == "url_not_from_sources"
    assert "Не составляй адреса сам" in result["text"] and calls == []


def test_search_results_page_links_and_user_urls_open(tmp_path: Path, fake_skill):
    calls, responses = fake_skill
    responses["search"] = {"ok": True, "text": "Found", "sources": [{"url": READ, "status": "discovered"}]}
    responses["fetch"] = lambda urls: {"ok": True, "text": "".join(f"[fetched: {u}]\nтекст\n" for u in urls),
                                       "pages": [_page(u, links=[{"label": "Связанная", "url": SERVED}]) for u in urls],
                                       "sources": [{"url": u, "status": "excerpt"} for u in urls]}
    _web.tool_web_search(tmp_path, queries=["давка Шымкент"])
    first = _web.tool_web_fetch(tmp_path, urls=[READ])
    assert first["page_numbers"] == {READ: 1} and f"[1] [fetched: {READ}]" in first["text"]
    second = _web.tool_web_fetch(tmp_path, urls=[SERVED])  # a link on the read page
    assert second["page_numbers"] == {SERVED: 2}
    with bind_runtime_context({"user_urls": json.dumps([DISCOVERED])}):
        third = _web.tool_web_fetch(tmp_path, urls=[DISCOVERED])  # the user's own address
    assert third["page_numbers"] == {DISCOVERED: 3}
    assert [urls for action, urls in calls if action == "fetch"] == [[READ], [SERVED], [DISCOVERED]]


def test_redirect_and_home_page_are_named_with_numbers(tmp_path: Path, fake_skill):
    _, responses = fake_skill
    responses["search"] = {"ok": True, "text": "Found", "sources": [{"url": u, "status": "discovered"}
                                                                    for u in (INVENTED, HOME)]}
    responses["fetch"] = {"ok": True, "text": (f"Fetched 2 pages in parallel:\n\n[fetched: {SERVED}]\nнасморк"
                                               f"\n\n———\n\n[fetched: {HOME}]\nЗаголовок: король прилетел"),
                          "pages": [_page(INVENTED, SERVED, "Невидимые соседи в квартире"),
                                    _page(HOME, title="Новости Казахстана", links=ARTICLES)],
                          "sources": [{"url": SERVED, "status": "excerpt"}, {"url": HOME, "status": "excerpt"}]}
    _web.tool_web_search(tmp_path, queries=["визит короля Бельгии"])
    result = _web.tool_web_fetch(tmp_path, urls=[INVENTED, HOME])
    assert result["redirected"] == [INVENTED] and result["index_pages"] == [HOME]
    assert result["page_numbers"] == {SERVED: 1}  # a home page is not a citable source
    assert f"Запрошен {INVENTED}, но сервер отдал другую страницу [1] «Невидимые соседи в квартире»" in result["text"]
    assert "«Новости Казахстана» — главная или рубрика: заголовки скрыты" in result["text"]
    assert "L1 Новость 1" in result["text"] and "король прилетел" not in result["text"]
    assert "[1] [fetched: " in result["text"] and "насморк" in result["text"]


def test_a_home_page_the_user_gave_is_read_not_hidden(tmp_path: Path, fake_skill):
    _, responses = fake_skill
    responses["fetch"] = {"ok": True, "text": f"[fetched: {HOME}]\nглавная zakon.kz",
                          "pages": [_page(HOME, title="Zakon.kz", links=ARTICLES)],
                          "sources": [{"url": HOME, "status": "excerpt"}]}
    with bind_runtime_context({"user_urls": json.dumps([HOME])}):
        result = _web.tool_web_fetch(tmp_path, urls=[HOME])
    assert result["index_pages"] == [] and result["page_numbers"] == {HOME: 1}
    assert "главная zakon.kz" in result["text"]


def test_budget_refusal_adds_no_page_notes(tmp_path: Path, fake_skill):
    _, responses = fake_skill
    responses["search"] = {"ok": True, "text": "Found", "sources": [{"url": READ, "status": "discovered"}]}
    responses["fetch"] = {"ok": False, "error": "web_time_budget", "text": "Лимит времени исследования исчерпан"}
    _web.tool_web_search(tmp_path, queries=["давка"])
    result = _web.tool_web_fetch(tmp_path, urls=[READ])
    assert result["text"] == "Лимит времени исследования исчерпан" and result["page_numbers"] == {}


def test_pages_open_by_label_and_unknown_labels_are_refused(tmp_path: Path, fake_skill):
    calls, responses = fake_skill
    responses["search"] = {"ok": True, "sources": [{"url": HOME, "status": "discovered"}],
                           "text": f"Found 1 results\n\n[1] Zakon.kz\n    {HOME}\n    [discovered]\n"}
    responses["fetch"] = lambda urls: {
        "ok": True, "text": "".join(f"[fetched: {u}]\nтекст\n" for u in urls),
        "pages": [_page(u, title="Zakon.kz", links=ARTICLES if u == HOME else ()) for u in urls],
        "sources": [{"url": u, "status": "excerpt"} for u in urls]}
    search = _web.tool_web_search(tmp_path, queries=["новости"])
    assert f"[S1] Zakon.kz\n    {HOME}" in search["text"]
    home = _web.tool_web_fetch(tmp_path, urls=["S1"])
    assert home["page_numbers"] == {} and home["index_pages"] == [HOME] and "L3 Новость 3" in home["text"]
    article = _web.tool_web_fetch(tmp_path, urls=["[L3]"])
    assert article["page_numbers"] == {ARTICLES[2]["url"]: 1}
    refused = _web.tool_web_fetch(tmp_path, urls=["L99"])
    assert refused["error"] == "url_not_from_sources" and "L99" in refused["text"]
    assert [urls for action, urls in calls if action == "fetch"] == [[HOME], [ARTICLES[2]["url"]]]
