"""Portable language regression from the b5a5fc5e games chat, without web/model I/O."""
from __future__ import annotations

from hashlib import sha256

import pytest

from app.application.code_agent.answer_language import answer_language_matches
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.task_outcomes import TaskOutcome
from app.application.web_evidence.receipts import format_source, make_source


URL = "https://box.co.uk/blog/october-game-releases"
GAME_NAMES = (
    "End of Abyss", "Ace Combat 8: Wings of Theve", "Star Wars: Galactic Racer",
    "Dragon's Dogma 2: Dark Arisen", "Castlevania: Belmont's Curse",
    "Final Fantasy Resonance", "Call of Duty: Modern Warfare 4", "Phantom Blade Zero",
)
COMPACT_GAMES_ANSWER = """Вот главные релизы на **PC и PS5** в ближайшие месяцы:

**Октябрь 2026:**
- 1 окт — End of Abyss (twin-stick / Metroidvania)
- 2 окт — Ace Combat 8: Wings of Theve (лётный экшен)
- 6 окт — Star Wars: Galactic Racer (гонки)
- 9 окт — Dragon's Dogma 2: Dark Arisen (экшен-RPG)
- 15 окт — Castlevania: Belmont's Curse (2D экшен)
- 22 окт — Final Fantasy Resonance (JRPG)
- 23 окт — Call of Duty: Modern Warfare 4 (FPS)
- 29 окт — Phantom Blade Zero (wuxia экшен-RPG)

**Ноябрь 2026:**
- GTA 6 (open-world, точная дата уточняется)

**Декабрь 2026:**
- 3 дек — Rayman Legends Retold (платформер)

Самое ожидаемое: GTA 6, Phantom Blade Zero, Modern Warfare 4, Final Fantasy Resonance.

[Box — October 2026 Game Releases](https://box.co.uk/blog/october-game-releases) | [StopGame — календарь](https://stopgame.ru/games/dates/2026/10)"""


def _presented_evidence(text: str) -> RunEvidence:
    evidence = RunEvidence()
    source = make_source(
        run_id="language-regression", tool="web_fetch", url=URL, status="excerpt",
        quote=text, content_hash=sha256(text.encode()).hexdigest(), offset=1500,
        fetched_at=1790878445.3416786, quote_verified=True,
    )
    packet = format_source(source)
    evidence.record_tool_result(
        tool_name="web_fetch", arguments={"url": URL}, execution_status="ok",
        output={"ok": True, "sources": [source]}, text_result=packet, state_changed=False,
    )
    evidence.mark_sources_presented([{"role": "tool", "content": packet}])
    return evidence


def test_source_bound_names_do_not_determine_games_list_language():
    evidence = _presented_evidence("\n".join(GAME_NAMES))
    assert answer_language_matches(COMPACT_GAMES_ANSWER, "ru", evidence.presented_sources) is True
    assert sha256(COMPACT_GAMES_ANSWER.encode()).hexdigest() == "ad27870b94edb937b8e65d6aca44839318f78359b3357158c00cfa018a4084d8"
    assert not answer_language_matches(COMPACT_GAMES_ANSWER, "ru", [])


@pytest.mark.parametrize("answer", [
    "Here are the upcoming games with release dates and recommendations. Привет.",
    "Here Are The Upcoming Games With Release Dates And Recommendations. Привет.",
    "Игры:\n- These games have excellent combat and beautiful visuals (recommended).",
    "Игры:\n- **These games have excellent combat and beautiful visuals** — recommended.",
    "Игры:\n| These games have excellent combat and beautiful visuals | recommended |",
])
def test_source_copied_english_prose_is_still_counted(answer):
    evidence = _presented_evidence(answer)
    assert not answer_language_matches(answer, "ru", evidence.presented_sources)


@pytest.mark.parametrize("field,value", [
    ("presented", False), ("quote_verified", False), ("status", "discovered"),
    ("tool", "web_search"), ("quote", "Names were not read."),
])
def test_unverified_unpresented_or_wrong_sources_cannot_exempt_names(field, value):
    evidence = _presented_evidence("\n".join(GAME_NAMES))
    sources = evidence.presented_sources
    sources[0][field] = value
    assert not answer_language_matches(COMPACT_GAMES_ANSWER, "ru", sources)


@pytest.mark.parametrize("answer", [
    "Игры на ближайшее время:\n1. End of Abyss (шутер)\n2. Phantom Blade Zero (экшен)",
    "Рекомендованные игры:\n- **End of Abyss** — шутер.\n- **Phantom Blade Zero**: экшен.",
    "Игры:\n- **Ace Combat 8: Wings of Theve** — стоит рассмотреть (лётный экшен).",
    "Список игр:\n| Игра | Жанр |\n| --- | --- |\n| End of Abyss | шутер |\n| Phantom Blade Zero | экшен |",
])
def test_named_list_and_table_fields_leave_russian_descriptions(answer):
    evidence = _presented_evidence("End of Abyss\nPhantom Blade Zero\nAce Combat 8: Wings of Theve")
    assert answer_language_matches(answer, "ru", evidence.presented_sources)


def test_source_name_match_requires_lexical_boundaries():
    answer = "Игра:\n- Ace Combat 8: Wings of Theve (экшен)"
    exact = _presented_evidence("Ace Combat 8: Wings of Theve")
    different = _presented_evidence("Ace Combat 8: Wings of Theves")
    assert answer_language_matches(answer, "ru", exact.presented_sources)
    assert not answer_language_matches(answer, "ru", different.presented_sources)


def test_oversized_data_label_is_not_exempt_even_when_source_bound():
    name = "Ace Combat " * 20
    evidence = _presented_evidence(name)
    assert not answer_language_matches(f"Игра:\n- {name}(экшен)", "ru", evidence.presented_sources)


@pytest.mark.parametrize("name", [
    "nginx", "nginx 1.29.2", "llama.cpp", "Node.js", "OpenSSL 3.5",
    "OpenSSL3.5", "Qwen3.8", "proxy_read_timeout", "CVE-2026-1234", "SearXNG",
])
@pytest.mark.parametrize("layout", ["- {name} (x)", "| {name} | x |"])
def test_sourced_technical_identifiers_and_versions_are_opaque_labels(name, layout):
    answer = "Сбой:\n" + layout.format(name=name)
    original = sha256(answer.encode()).hexdigest()
    evidence = _presented_evidence(name)
    assert answer_language_matches(answer, "ru", evidence.presented_sources) is True
    assert not answer_language_matches(answer, "ru", [])
    assert sha256(answer.encode()).hexdigest() == original


@pytest.mark.parametrize("prose", [
    "nginx requires a security update", "Node.js requires a security update",
    "OpenSSL 3.5 requires a security update", "llama.cpp uses a vulnerable dependency",
])
def test_sourced_technical_english_explanations_are_not_opaque_names(prose):
    answer = f"Сбой:\n- {prose} (recommended)"
    evidence = _presented_evidence(prose)
    assert not answer_language_matches(answer, "ru", evidence.presented_sources)


@pytest.mark.parametrize("answer,language,expected", [
    ("", "ru", False),
    ("123 --", "ru", False),
    ("Русский ответ с обычным объяснением.", "ru", True),
    ("An English answer with a normal explanation.", "en", True),
    ("Русский ответ с обычным объяснением.", "en", False),
    ("An English answer with a normal explanation.", "ru", False),
    ("Русский ответ с обычным объяснением.", "unknown", False),
    ("[English source title](https://example.org)\n`English code`", "ru", False),
    ("> A quoted English sentence.\n\nОтвет основан на прочитанном источнике.", "ru", True),
])
def test_language_uses_remaining_prose_and_requires_script(answer, language, expected):
    assert answer_language_matches(answer, language, []) is expected
