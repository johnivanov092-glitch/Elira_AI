"""Each web answer point must come from the page it cites and from the asked period
(KZ runs 648a0f64 / 98a78e8c, 2026-10-11)."""
from __future__ import annotations

from datetime import date

from app.application.code_agent.answer_acceptance import AnswerAcceptance
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.task_outcomes import TaskOutcome
from app.application.code_agent.web_points import point_problems, remove_unverified_points

ROYAL = "https://www.zakon.kz/politika/6534612-korol-belgii-prileteli.html"
STORM = "https://www.zakon.kz/nauka/6534599-moshchnaya-magnitnaya-burya.html"
CASPIAN = "https://regtv.kz/2026/09/24/gibel-voennosluzhashchikh-na-kaspii.html"
TEXTS = {
    ROYAL: "[2] [fetched: royal] Король Бельгии Филипп и королева Матильда прилетели в Астану. "
           "Токаев встретил высоких гостей в аэропорту. Визит продлится с 11 по 14 октября.",
    STORM: "[3] [fetched: storm] Мощная магнитная буря обрушилась на Землю, учёные предупредили о последствиях "
           "для самочувствия и связи.",
    CASPIAN: "[4] [fetched: caspian] Во время учений на Каспии погибли 12 военнослужащих, возбуждено уголовное дело.",
}
DATES = {ROYAL: "2026-10-11", STORM: "2026-10-11", CASPIAN: "2026-09-24"}
TODAY = date(2026, 10, 11)


def test_a_point_from_its_page_passes_and_a_borrowed_headline_fails():
    answer = (f"- Король Филипп и королева Матильда прилетели в Астану, Токаев встретил их в аэропорту [zakon]({ROYAL}).\n"
              f"- Казахстан решительно осудил атаки хуситов на объекты Саудовской Аравии [zakon]({STORM}).")
    problems = point_problems(answer, TEXTS, DATES, "Новости Казахстана", TODAY)
    assert [problem["line"] for problem in problems] == [1]
    assert "хуситов" in problems[0]["missing"]


def test_a_number_absent_from_the_page_fails():
    answer = f"- На Каспии погибли 15 военнослужащих, возбуждено уголовное дело [regtv]({CASPIAN})."
    assert point_problems(answer, TEXTS, DATES, "Что случилось на Каспии?", TODAY)[0]["missing"] == ["15"]


def test_an_article_older_than_the_asked_week_is_out_of_period():
    answer = f"- На Каспии погибли 12 военнослужащих, возбуждено уголовное дело [regtv]({CASPIAN})."
    problems = point_problems(answer, TEXTS, DATES, "Новости Казахстана за последнюю неделю", TODAY)
    assert problems == [{"line": 0, "excerpt": problems[0]["excerpt"], "missing": [], "old": ["2026-09-24"]}]
    assert point_problems(answer, TEXTS, DATES, "Что случилось на Каспии?", TODAY) == []
    digest = f"## ЧП\n{answer}\n\n## Политика\n- Король прилетел в Астану [zakon]({ROYAL})."
    cleaned, removed = remove_unverified_points(digest, point_problems(
        digest, TEXTS, DATES, "Новости Казахстана за последнюю неделю", TODAY), set())
    assert removed == 1 and "## ЧП" not in cleaned and "Каспии" not in cleaned  # empty section goes too
    assert cleaned.startswith("## Политика\n- Король прилетел") and "Убрано пунктов: 1" in cleaned


def test_acceptance_asks_once_then_marks_the_point():
    evidence = RunEvidence()
    evidence.record_tool_result(
        tool_name="web_fetch", arguments={"urls": ["L3", "L6"]}, execution_status="ok",
        output={"ok": True, "page_numbers": {ROYAL: 1, STORM: 2},
                "page_texts": {ROYAL: TEXTS[ROYAL], STORM: TEXTS[STORM]},
                "page_dates": {ROYAL: DATES[ROYAL], STORM: DATES[STORM]},
                "pages": [{"url": ROYAL, "final_url": ROYAL, "title": "Король", "ok": True},
                          {"url": STORM, "final_url": STORM, "title": "Буря", "ok": True}],
                "sources": [{"url": ROYAL, "status": "excerpt"}, {"url": STORM, "status": "excerpt"}]},
        text_result="", state_changed=False)
    answer = ("- Король Филипп и королева Матильда прилетели в Астану, Токаев встретил их в аэропорту [1].\n"
              "- Казахстан решительно осудил атаки хуситов на объекты Саудовской Аравии [2].")
    owner = AnswerAcceptance()

    def evaluate(step):
        return owner.evaluate(
            final_text=answer, raw_user_message="Новости Казахстана", pending_redirected_jobs=(),
            active_capability_groups=set(), task_outcome=TaskOutcome(), run_evidence=evidence,
            code_input_epoch=0, quote_word_limit=None, step=step, run_id="kz-points")
    first = evaluate(4)
    assert first.action == "retry" and first.reason == "web_source" and "хуситов" in first.correction
    owner.commit(first)
    second = evaluate(5)
    assert second.action == "accept" and second.answer_status == "degraded"
    assert second.text.split("\n")[0].endswith(f"[zakon.kz]({ROYAL}).")
    assert "хуситов" not in second.text and "Убрано пунктов: 1" in second.text
