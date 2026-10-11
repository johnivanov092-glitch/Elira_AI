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
    assert problems == [{"line": 0, "excerpt": problems[0]["excerpt"], "missing": [], "old": ["2026-09-24"],
                         "weak": []}]
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
    assert second.text.split("\n")[0].endswith(f"[zakon.kz, 11.10.2026]({ROYAL}).")  # the page's date
    assert "хуситов" not in second.text and "Убрано пунктов: 1" in second.text


def test_a_russian_table_row_about_an_english_page_keeps_its_main_figure():
    # SWE run 57ad0d5a: the row was dropped for 95/256 and Russian wording.
    card = "https://huggingface.co/Qwen/Qwen3.8-Flash-Next"
    texts = {card: "[2] [fetched: card] Benchmark | Qwen3.8-Flash-Next\nSWE-Bench Multilingual | 81.0\n"
                   "SWE-Bench Pro | 62.5 (mini-SWE-agent)"}
    row = (f"| **Qwen3.8-Flash-Next** | **81.0%** (с harness mini-SWE-agent, temp=1.0, top_p=0.95, 256K контекст) "
           f"| [huggingface.co]({card}) — официальная страница модели Hugging Face / Qwen |")
    assert point_problems(row, texts, {}, "Есть ли у Qwen результаты на SWE-bench Multilingual?", TODAY) == []
    invented = f"- Qwen3.8-Flash-Next набрала 91.5% на SWE-bench Multilingual [huggingface.co]({card})."
    assert point_problems(invented, texts, {}, "Есть ли у Qwen результаты?", TODAY)[0]["missing"] == ["91.5"]


def test_an_extra_link_that_does_not_carry_the_point_is_stripped_not_the_point():
    # KZ run at 3f41a4e: a kept point carried an unrelated second article (24.kz flu).
    answer = (f"## Политика\n"
              f"- Король Филипп и королева Матильда прилетели в Астану, Токаев встретил их в аэропорту "
              f"[zakon]({ROYAL}), [zakon]({STORM}).")
    problems = point_problems(answer, TEXTS, DATES, "Новости Казахстана за неделю", TODAY)
    assert [(problem["line"], problem["missing"], problem["weak"]) for problem in problems] == [(1, [], [STORM])]
    cleaned, removed = remove_unverified_points(answer, problems, set())
    assert removed == 1 and STORM not in cleaned and ROYAL in cleaned
    assert "Король Филипп" in cleaned and "Убрано пунктов" not in cleaned  # a link went, no point did


def test_a_heading_left_empty_before_a_rule_goes_too():
    answer = (f"## Политика\n- Король прилетел в Астану [zakon]({ROYAL}).\n\n"
              f"## Прочее за неделю\n- Казахстан осудил атаки хуситов на объекты Саудовской Аравии [zakon]({STORM}).\n\n"
              f"---\nИсточники выше.")
    problems = point_problems(answer, TEXTS, DATES, "Новости Казахстана", TODAY)
    cleaned, removed = remove_unverified_points(answer, problems, set())
    assert removed == 1 and "## Прочее за неделю" not in cleaned and "хуситов" not in cleaned
    assert "## Политика" in cleaned and "---" in cleaned
    # KZ replay d5904cc4: a title over subsections stays; over emptied ones it goes.
    nested = (f"## Новости за неделю\n\n### Политика\n- Король прилетел в Астану [zakon]({ROYAL}).\n\n"
              f"## Прочее\n### Мир\n- Казахстан осудил атаки хуситов на объекты Саудовской Аравии [zakon]({STORM}).")
    cleaned, _ = remove_unverified_points(nested, point_problems(nested, TEXTS, DATES, "Новости", TODAY), set())
    assert cleaned.startswith("## Новости за неделю\n\n### Политика") and "## Прочее" not in cleaned
    assert "### Мир" not in cleaned


def test_a_point_on_another_read_page_is_recited_there():
    # KZ run d5904cc4: the king's visit was read as [6], the answer kept [2][4].
    from app.application.code_agent.web_points import reattribute_points
    answer = (f"- Король Филипп и королева Матильда прилетели в Астану, Токаев встретил их в аэропорту "
              f"[zakon]({STORM}), [regtv]({CASPIAN}).\n"
              f"- Казахстан осудил атаки хуситов на объекты Саудовской Аравии [zakon]({STORM}).")
    request = "Новости Казахстана за неделю"
    moved_text, moved = reattribute_points(answer, TEXTS, DATES, request, TODAY)
    assert moved == 1
    assert moved_text.split("\n")[0].endswith(f"аэропорту [zakon.kz]({ROYAL}).")
    assert [problem["line"] for problem in point_problems(moved_text, TEXTS, DATES, request, TODAY)] == [1]
    # An out-of-period page is never the new source.
    caspian = f"- На Каспии погибли 12 военнослужащих, возбуждено уголовное дело [zakon]({STORM})."
    assert reattribute_points(caspian, TEXTS, DATES, request, TODAY) == (caspian, 0)
    assert reattribute_points(caspian, TEXTS, DATES, "Что случилось на Каспии?", TODAY)[1] == 1


def test_a_point_moves_only_to_a_page_that_plainly_carries_it():
    # KZ run 027209f0: "резервы сократились на 5,4%" went to a flu article (its 5 and 4 matched).
    from app.application.code_agent.web_points import reattribute_points
    flu = "https://24.kz/ru/news/798040-pervye-sluchai-grippa"
    texts = {flu: "[2] [fetched: flu] Первые случаи гриппа выявили в двух регионах в начале октября; "
                  "заболеваемость ОРВИ за неделю снизилась на 10,2%, сократились обращения. 5 случаев, 4 региона."}
    answer = f"- Международные резервы в сентябре сократились на 5,4%, с начала года выросли на 1,5% [kz]({ROYAL})."
    assert reattribute_points(answer, {**TEXTS, **texts}, DATES, "Новости Казахстана", TODAY) == (answer, 0)
    assert point_problems(answer, {**TEXTS, **texts}, DATES, "Новости", TODAY)[0]["missing"][:2] == ["5.4", "1.5"]


def test_a_list_item_without_a_link_is_checked_against_every_read_page():
    # KZ run 027209f0: "[5]" named no read page, lost its marker and the point stayed unsourced.
    from app.application.code_agent.web_points import reattribute_points
    answer = ("**Политика**\n"
              "- За неделю полицейские пресекли 201 факт пьяного вождения в Астане и Шымкенте\n\n"
              "**ЧП**\n"
              "- Во время учений на Каспии погибли 12 военнослужащих, возбуждено уголовное дело.\n"
              "  - Король Бельгии Филипп прилетел в Астану\n"
              "```\n- 999 строк кода не проверяются\n```")
    problems = point_problems(answer, TEXTS, DATES, "Что случилось в Казахстане?", TODAY)
    assert [(problem["line"], problem.get("unsourced")) for problem in problems] == [(1, True)]
    linked, moved = reattribute_points(answer, TEXTS, DATES, "Что случилось в Казахстане?", TODAY)
    assert moved == 1 and f"уголовное дело [regtv.kz]({CASPIAN})." in linked
    assert "  - Король Бельгии Филипп прилетел в Астану\n" in linked  # a nested item gets no link
    cleaned, removed = remove_unverified_points(linked, point_problems(
        linked, TEXTS, DATES, "Что случилось в Казахстане?", TODAY), set())
    assert removed == 1 and "201" not in cleaned and "**Политика**" not in cleaned  # the bold title went too
    assert cleaned.startswith("**ЧП**") and "999 строк" in cleaned


def test_bare_addresses_become_links_the_checks_see():
    # NGINX runs 091ef13b / f30050e9: "— https://my.f5.com/…" named an unread page past every check.
    from app.application.code_agent.web_points import link_bare_urls
    text = ("1. Бюллетень — https://nginx.org/en/security_advisories.html (обновлено).\n"
            "2. NVD: https://nvd.nist.gov/vuln/detail/CVE-2026-42530, `curl https://x.org/a` и [ok](https://ok.org/b)\n"
            "```\nhttps://code.org/c\n```")
    assert link_bare_urls(text) == (
        "1. Бюллетень — [nginx.org](https://nginx.org/en/security_advisories.html) (обновлено).\n"
        "2. NVD: [nvd.nist.gov](https://nvd.nist.gov/vuln/detail/CVE-2026-42530), `curl https://x.org/a` "
        "и [ok](https://ok.org/b)\n```\nhttps://code.org/c\n```")
    twice = f"- [zakon.kz]({ROYAL}) https://www.zakon.kz/politika/6534612-korol-belgii-prileteli.html — визит"
    assert link_bare_urls(twice) == f"- [zakon.kz]({ROYAL}) — визит"  # the list marker stays
    answer = f"Проверено: данные с официальной страницы [zakon]({ROYAL}) и вторым источником [x]({STORM})."
    cleaned, _ = remove_unverified_points(answer, [], {STORM})
    assert cleaned.endswith("вторым источником.")  # no gap where the link was


def test_read_links_show_the_publication_date_and_a_false_verified_line_goes():
    from app.application.code_agent.web_points import date_read_links
    text = f"- Визит [zakon.kz]({ROYAL}), буря [Буря, 2026-10-11]({STORM}), прочее [x](https://x.kz/a)."
    assert date_read_links(text, DATES) == (
        f"- Визит [zakon.kz, 11.10.2026]({ROYAL}), буря [Буря, 2026-10-11]({STORM}), прочее [x](https://x.kz/a).")
    answer = (f"Проверено: визит и атаки хуситов подтверждены.\n\n## Политика\n"
              f"- Король Филипп прилетел в Астану, Токаев встретил его в аэропорту [zakon]({ROYAL}).\n"
              f"- Казахстан осудил атаки хуситов на объекты Саудовской Аравии [zakon]({STORM}).")
    cleaned, removed = remove_unverified_points(
        answer, point_problems(answer, TEXTS, DATES, "Новости Казахстана", TODAY), set())
    assert removed == 1 and "Проверено" not in cleaned and cleaned.startswith("## Политика")


def test_search_labels_become_numbers_or_unread_links():
    from app.application.code_agent.web_points import resolve_web_labels
    labels = {"S9": ROYAL, "S10": STORM}
    text = resolve_web_labels("- Визит короля [S9, S10].", labels, [(ROYAL, "Король")])
    assert text == f"- Визит короля [1], [S10]({STORM})."
