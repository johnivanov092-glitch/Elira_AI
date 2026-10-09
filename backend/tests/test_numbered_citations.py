"""John 2026-10-06 (industry practice): the model cites read pages by number; Elira renders the links."""
from copy import deepcopy

import pytest

from app.application.code_agent import agent_loop
from webskill.application.code_agent.answer_contracts import render_numbered_citations
from webskill.application.code_agent.tools import _web
from webskill.infrastructure.search.web_runtime import PageFetchResult

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
