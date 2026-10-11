"""Links to unread pages lose their address (John 2026-10-06), and web_fetch says
when the server served a different page than requested (KZ run 319d7f05, 11.10)."""
from __future__ import annotations

from pathlib import Path

from app.application.code_agent.answer_acceptance import AnswerAcceptance
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.task_outcomes import TaskOutcome
from app.application.code_agent.tools import _web

READ = "https://www.nur.kz/incident/crime/2441011-davka-na-koncerte/"
FAILED = "https://informburo.kz/novosti/pozar-v-astane"
DISCOVERED = "https://www.zakon.kz/sport/6534596-skonchalsya-vratar.html"
INVENTED = "https://www.zakon.kz/politics/6534578-korol-belgii-posetit-kazahstan.html"
SERVED = "https://www.zakon.kz/sovety/6534578-nevidimye-sosedi-v-kvartire.html"


def _evidence() -> RunEvidence:
    evidence = RunEvidence()
    evidence.record_tool_result(
        tool_name="web_search", arguments={"queries": ["новости Казахстана"]}, execution_status="ok",
        output={"ok": True, "sources": [{"url": DISCOVERED, "status": "discovered"},
                                         {"url": READ, "status": "discovered"}]},
        text_result="", state_changed=False)
    # One page failed in the same call: the read page must still count as read.
    evidence.record_tool_result(
        tool_name="web_fetch", arguments={"urls": [READ, FAILED, INVENTED]}, execution_status="ok",
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


def test_answer_keeps_read_links_and_marks_the_rest():
    evidence = _evidence()
    violations = evidence.unread_web_links(ANSWER)
    marked = evidence.mark_unread_web_links(ANSWER, violations)
    assert f"]({READ})" in marked
    for url in (FAILED, DISCOVERED, INVENTED):
        assert url not in marked
    assert "Informburo (ссылка убрана: не открылась — код 404)" in marked
    assert "Zakon (ссылка убрана: страница не прочитана)" in marked
    assert "Давка на концерте" in marked and "Король Бельгии" in marked  # the answer itself stays


def test_acceptance_marks_unread_links_and_reports_partial():
    decision = AnswerAcceptance().evaluate(
        final_text=ANSWER, raw_user_message="Новости Казахстана за неделю.",
        pending_redirected_jobs=(), active_capability_groups=set(), task_outcome=TaskOutcome(),
        run_evidence=_evidence(), code_input_epoch=0, quote_word_limit=None, step=3, run_id="kz-links")
    assert decision.action == "accept" and decision.answer_status == "degraded"
    assert INVENTED not in decision.text and f"]({READ})" in decision.text


def test_answer_without_web_research_is_untouched():
    text = "Документация: [docs](https://docs.python.org/3/)"
    decision = AnswerAcceptance().evaluate(
        final_text=text, raw_user_message="Где документация Python?",
        pending_redirected_jobs=(), active_capability_groups=set(), task_outcome=TaskOutcome(),
        run_evidence=RunEvidence(), code_input_epoch=0, quote_word_limit=None, step=1, run_id="no-web")
    assert decision.text == text and decision.answer_status == "complete"


def test_web_fetch_names_a_redirect_to_another_page(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(_web, "_call", lambda *args, **kwargs: {
        "ok": True, "text": f"[fetched: {SERVED}]\nНевидимые соседи в квартире…",
        "sources": [{"url": SERVED, "status": "excerpt"}, {"url": "https://www.nur.kz/", "status": "excerpt"}]})
    result = _web.tool_web_fetch(tmp_path, urls=[INVENTED, "http://nur.kz"])
    assert result["redirected"] == [INVENTED]
    assert f"Адрес {INVENTED} не открылся как запрошено" in result["text"]
    assert "http://nur.kz" not in result["text"].split("[fetched")[-1].split("Невидимые")[0]


def test_web_fetch_without_pages_adds_no_redirect_note(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(_web, "_call", lambda *args, **kwargs: {
        "ok": False, "error": "web_time_budget", "text": "Лимит времени исследования исчерпан"})
    result = _web.tool_web_fetch(tmp_path, urls=[INVENTED])
    assert "redirected" not in result and "не открылся как запрошено" not in result["text"]
