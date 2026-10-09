"""Citation coverage for release/update cadence, not a semantic fact checker."""

import pytest

from webskill.application.code_agent.answer_contracts import (
    web_cadence_citation_correction,
    web_cadence_citation_violations,
)


READ_URL = "https://developer.mozilla.org/en-US/docs/Mozilla/Firefox#firefox_extended_support_release_esr"
SOURCE_ID = "w_a7283689ccb4340ea43e"
# Exact adjacent bullets from the captured 20260919 release-index-guidance run.
# The ESR citation must not cover the preceding Stable claim.
AUDIT_BULLETS = (
    "- **Stable** получает новую основную версию каждый релизный цикл (~4 недели), "
    "поэтому счётчик быстро идёт вверх: 154 → 155 → 156.\n"
    "- **ESR** — это «закреплённая» ветка долгосрочной поддержки: она берёт одну "
    "основную версию (здесь 153) и держит её долго, выпуская только точечные "
    "обновления с поправками безопасности и стабильности — отсюда `153.1.0`, "
    "`153.2.0`, `153.3.0` [[source:w_a7283689ccb4340ea43e]]."
)


def check(text, *, ids=(SOURCE_ID,), urls=(READ_URL,), sources=None):
    return web_cadence_citation_violations(
        text, matched_source_ids=ids, read_source_urls=urls, read_sources=sources,
    )


def test_captured_uncited_stable_bullet_is_not_covered_by_adjacent_esr_source():
    failures = check(AUDIT_BULLETS)
    assert len(failures) == 1
    assert failures[0].block_index == 1
    assert failures[0].quantity == "4 недели"
    correction = web_cadence_citation_correction(failures)
    assert "объект" in correction
    assert "канал" in correction
    assert "убери" in correction


@pytest.mark.parametrize("claim", [
    "Обновления выходят каждые 2 недели.",
    "Новая версия выпускается раз в 30 дней.",
    "The update cadence is 4 weeks.",
    "The stable channel releases a new version every 2 weeks.",
    "The project releases 2 updates per month.",
    "Ветка получает 2 обновления в месяц.",
    "Stable is updated every 4 weeks.",
    "Ветка обновляется каждые 4 недели.",
    "Обновления выходят каждые **4** недели.",
    "Обновления выходят каждые `4` недели.",
    "Обновления выходят каждые `4 недели`.",
    "Обновления выходят каждые две недели.",
    "New versions are released every four weeks.",
])
def test_numeric_update_durations_and_rates_need_local_read_reference(claim):
    assert len(check(claim)) == 1
    assert not check(claim + f" [[source:{SOURCE_ID}]]")
    assert not check(claim + f" [Documentation]({READ_URL})")
    assert not check(claim + " " + READ_URL)


@pytest.mark.parametrize("claim", [
    "Версия 156.0 опубликована 15 сентября 2026 года.",
    "Stable 156.0; ESR 153.3.0.",
    "Цена обновления составляет 4 доллара, скидка 20%.",
    "Проверка заняла 4 секунды; выполнено 2 локальных теста.",
    "За последние 4 недели вышли обновления.",
    "За последние 4 недели вышли 3 обновления; точный период выпуска пока не подтверждён.",
    "Обновления выходят регулярно; точный период не подтверждён.",
    "```text\nThe update cadence is 4 weeks.\n```",
    "Пример кода: `update_every_4_weeks()`.",
])
def test_versions_dates_prices_timings_history_and_code_are_outside_contract(claim):
    assert not check(claim)


def test_unread_url_fake_marker_code_marker_and_separate_source_list_do_not_cover():
    claim = "Обновления выходят каждые 4 недели."
    assert check(claim + " [[source:unknown]]")
    assert check(claim + f" `[[source:{SOURCE_ID}]]`")
    assert check(claim + " [Source](https://unread.example/policy)")
    assert check(claim + "\n\nИсточники:\n" + READ_URL)
    assert check(claim + "\n## Источники\n" + READ_URL)
    assert check(claim + f"\n> Отдельная цитата [[source:{SOURCE_ID}]]")
    assert not check(claim + "\n" + READ_URL)


def test_wrapped_bullet_and_table_rows_preserve_local_reference_scope():
    text = (
        "- Обновления выходят каждые 4 недели,\n"
        f"  как указано в документации [[source:{SOURCE_ID}]].\n"
        "- Другой канал выпускает обновления каждые 2 недели."
    )
    assert [item.block_index for item in check(text)] == [2]
    table = (
        "| Канал | Период | Источник |\n| --- | --- | --- |\n"
        f"| Alpha | Обновления каждые 4 недели | [[source:{SOURCE_ID}]] |\n"
        "| Beta | Обновления каждые 2 недели | |"
    )
    assert len(check(table)) == 1


def test_live_worded_cadence_cannot_be_supported_by_version_index():
    # Exact cadence bullet from ordinary UI run 84cc90fa31524fd2b5bcd83a30c060ef.
    ids = ("w_090aeceb28ef923b2eb7", "w_1f3e897ed2735b663dff")
    claim = (
        "- **Обычный Firefox** выходит часто: в источнике указано, что стабильный "
        "канал получает новые версии примерно **каждые две недели**.  \n"
        "  [[source:w_090aeceb28ef923b2eb7]][[source:w_1f3e897ed2735b663dff]]"
    )
    # Excerpts in that run contain release numbers only, with no time unit.
    sources = [
        {"id": ids[0], "url": "https://www.firefox.com/en-US/releases/",
         "quote": "Firefox\nReleases\n156.0\n155.0\n155.0.1\n154.0\n153.3.0 ESR"},
        {"id": ids[1], "url": "https://www.firefox.com/en-US/releases/",
         "quote": "0\n121.0.1\n120.0\n120.0.1\n119.0\n119.0.1\n118.0"},
    ]
    violations = check(claim, ids=ids, sources=sources)
    assert len(violations) == 1
    assert violations[0].quantity == "две недели"
    assert violations[0].reason == "missing_quantity"
    assert "величина" in web_cadence_citation_correction(violations)


@pytest.mark.parametrize("source_quote", [
    "New versions are released every two weeks.",
    "New versions are released every 2 weeks.",
    "Новые версии выходят каждые две недели.",
])
def test_equivalent_worded_quantity_and_time_unit_can_cover_a_cadence(source_quote):
    source = {"id": SOURCE_ID, "url": READ_URL, "quote": source_quote}
    claim = "Обновления выходят каждые две недели."
    assert not check(claim + f" [[source:{SOURCE_ID}]]", sources=[source])
    assert not check(claim + f" [Source]({READ_URL})", sources=[source])


@pytest.mark.parametrize("source_quote", [
    "New versions are released every four weeks.",
    "New versions are released every two months.",
    "New versions are released every fourteen days.",
    "2.0\nThe project has existed for weeks.",
    "There are two releases per week.",
])
def test_cited_excerpt_needs_the_same_quantity_unit_and_duration_kind(source_quote):
    source = {"id": SOURCE_ID, "url": READ_URL, "quote": source_quote}
    violations = check("Обновления выходят каждые две недели. "
                       f"[[source:{SOURCE_ID}]]", sources=[source])
    assert len(violations) == 1
    assert violations[0].reason == "missing_quantity"


def test_uncited_matching_excerpt_does_not_fill_an_unrelated_citation():
    sources = [
        {"id": SOURCE_ID, "url": READ_URL, "quote": "New versions every four weeks."},
        {"id": "other", "url": "https://read.example/policy", "quote": "New versions every two weeks."},
    ]
    assert check("Обновления выходят каждые две недели. "
                 f"[[source:{SOURCE_ID}]]", sources=sources)
