"""Search ranking through the actual native tool with only HTTP stubbed."""
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools._web import tool_web_search
from app.core.web_runtime import result_score
from test_web_search_engine_warnings import _http


def test_relevant_candidate_after_raw_limit_survives_before_final_cutoff():
    payload = {"results": [
        {"url": "https://music.example.org/song", "title": "Song lyrics", "content": "Popular music"},
        {"url": "https://sport.example.org/latest", "title": "Football scores"},
        {"url": "https://travel.example.org/hotels", "title": "Holiday hotels"},
        {"url": "https://docs.example.org/fastapi", "title": "FastAPI websocket authentication guide",
         "content": "FastAPI websocket authentication and API security"},
    ], "unresponsive_engines": []}
    with _http(payload) as calls:
        result = tool_web_search(query="FastAPI websocket authentication", top_k=1)
    assert len(calls) == 1
    assert [source["url"] for source in result["sources"]] == ["https://docs.example.org/fastapi"]


def test_full_unrelated_news_cannot_discard_useful_general_fallback():
    def reply(params):
        if params["categories"] == "news":
            return {"results": [
                        {"url": f"https://sport.example.org/{number}", "title": f"Football latest scores {number}"}
                        for number in range(3)],
                    "unresponsive_engines": [["bing news", "parsing error"]]}
        return {"results": [{"url": "https://science.example.org/bluebird", "title": "Bluebird trial publication"}],
                "unresponsive_engines": []}
    with _http(reply=reply) as calls:
        result = tool_web_search(query="Bluebird trial", categories="news", time_range="week", top_k=1)
    assert [call["params"]["categories"] for call in calls] == ["news", "general"]
    assert all(call["params"]["time_range"] == "week" for call in calls)
    assert [source["url"] for source in result["sources"]] == ["https://science.example.org/bluebird"]
    assert result["engine_warnings"] == [{"engine": "bing news", "error": "parsing error"}]
    assert "категория: general" in result["text"]


def test_calendar_matches_cannot_outweigh_subject_match():
    subject = {"title": "Bluebird trial report", "body": "", "href": "https://science.example.org/trial", "engine": "searxng"}
    calendar = {"title": "Travel calendar October 2026", "body": "", "href": "https://travel.example.org/calendar", "engine": "searxng"}
    assert result_score(subject, query="Bluebird 3 October 2026") > result_score(calendar, query="Bluebird 3 October 2026")


def test_russian_subject_and_geography_forms_beat_wrong_location_dates():
    useful = {"title": "Пожар в Москве локализован", "body": "", "href": "https://local.example.org/fire", "engine": "searxng"}
    wrong_location = {"title": "Спортивный календарь 3 октября 2026", "body": "", "href": "https://sport.example.org/calendar", "engine": "searxng"}
    query = "пожары Москва 3 октября 2026"
    assert result_score(useful, query=query) > result_score(wrong_location, query=query)


def test_site_constrained_candidate_after_raw_limit_is_not_lost():
    payload = {"results": [
        {"url": "https://other.example.org/guide", "title": "FastAPI security guide"},
        {"url": "https://other.example.org/guide2", "title": "FastAPI security guide"},
        {"url": "https://other.example.org/guide3", "title": "FastAPI security guide"},
        {"url": "https://docs.example.org/guide", "title": "FastAPI security guide"},
    ], "unresponsive_engines": []}
    with _http(payload) as calls:
        result = tool_web_search(query="FastAPI security site:docs.example.org", top_k=1)
    assert len(calls) == 1
    assert [source["url"] for source in result["sources"]] == ["https://docs.example.org/guide"]


@pytest.mark.parametrize(("query", "title", "body"), [
    ("гемоглобин результаты анализа", "Снижение гемоглобина в анализах крови", "Клиническое исследование"),
    ("CRISPR off target detection", "CRISPR off target detection methods", "Genomic sequencing study"),
])
def test_medicine_and_science_candidates_survive_calendar_noise(query, title, body):
    payload = {"results": [
        {"url": f"https://calendar.example.org/{number}", "title": "Calendar 3 October 2026"}
        for number in range(3)
    ] + [{"url": "https://research.example.org/study", "title": title, "content": body}],
        "unresponsive_engines": []}
    with _http(payload) as calls:
        result = tool_web_search(query=query + " 3 October 2026", categories="science", top_k=1)
    assert len(calls) == 1 and calls[0]["params"]["categories"] == "science"
    assert [source["url"] for source in result["sources"]] == ["https://research.example.org/study"]


@pytest.mark.parametrize(("query", "useful_title", "wrong_title"), [
    ("Vitamin D threshold 30 ng/ml", "Vitamin D threshold 30 ng/ml", "Vitamin D threshold 5 ng/ml"),
    ("OpenSSL 3.0.16 CVE-2026-429", "OpenSSL 3.0.16 CVE-2026-429", "OpenSSL 1.0.2 CVE-2025-200"),
])
def test_clinical_values_and_version_numbers_remain_subject_terms(query, useful_title, wrong_title):
    useful = {"title": useful_title, "href": "https://source.example.org/one", "engine": "searxng"}
    wrong = {"title": wrong_title, "href": "https://source.example.org/two", "engine": "searxng"}
    assert result_score(useful, query=query) > result_score(wrong, query=query)


def test_semantic_only_candidates_keep_upstream_order_and_are_not_discarded():
    urls = ["https://medical.example.org/z", "https://medical.example.org/a"]
    payload = {"results": [
        {"url": urls[0], "title": "Zinc supplementation study", "content": "Haemoglobin concentrations"},
        {"url": urls[1], "title": "Anaemia clinical assessment", "content": "Erythrocyte reference intervals"},
    ], "unresponsive_engines": []}
    with _http(payload):
        result = tool_web_search(query="blood test interpretation", top_k=2)
    assert result["ok"] and [source["url"] for source in result["sources"]] == urls


def test_decoded_url_terms_can_identify_a_terse_result_without_rewriting_url():
    url = "https://docs.example.org/%D0%BF%D0%BE%D0%B6%D0%B0%D1%80%D1%8B/%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0?q=x%26y"
    payload = {"results": [
        {"url": "https://other.example.org/guide", "title": "Guide"},
        {"url": url, "title": "Guide"},
    ], "unresponsive_engines": []}
    with _http(payload):
        result = tool_web_search(query="пожар Москва", top_k=1)
    assert result["sources"][0]["url"] == url


def test_saved_run_wrong_country_and_calendar_candidates_cannot_displace_subject_and_geography():
    # Titles/snippets captured in original-network tool_call step 1/2. The
    # upstream candidate tail was not saved, so this verifies their ranking only.
    payload = {"results": [
        {"url": "https://www.m24.ru/videos/03102026/948285",
         "title": 'На Воробьевых горах проходит полумарафон "Моя столица"',
         "content": 'Беговой сезон в столице закрывают полумарафоном "Моя столица". 3 октября участники преодолевают небольшие дистанции – 500 ...'},
        {"url": "https://fakty.com.ua/ru/proisshestvija/20261002-vybuhy-u-dnipri/amp/",
         "title": "Взрывы в Днепре: что произошло сегодня, 2 октября 2026 | Факты ICTV",
         "content": "Напомним, что 2 октября РФ почти 20 раз ударила дронами по Днепропетровской области, есть пострадавший."},
        {"url": "https://glagol.press/ovechkin-obnovil-svoj-antirekord-v-nhl",
         "title": "Овечкин обновил свой антирекорд в НХЛ",
         "content": "Российский капитан «Вашингтон» Александр Овечкин провел в матче против «Каролины» менее 13 минут."},
        {"url": "https://ru.wikipedia.org/wiki/Caspian_incident",
         "title": "Гибель военнослужащих в Каспийском море — Википедия",
         "content": "Гибель военнослужащих в Каспийском море произошла 24 сентября 2026 года в Мунайлинском районе Мангистауской области Казахстана, во время плановых военных учений ..."},
    ], "unresponsive_engines": []}
    with _http(payload):
        result = tool_web_search(query="Казахстан ЧП 3 октября 2026", top_k=1)
    assert result["sources"][0]["url"] == "https://ru.wikipedia.org/wiki/Caspian_incident"


def test_generic_news_portal_cannot_outweigh_article_subject_and_geography():
    payload = {"results": [
        {"url": "https://informburo.kz/", "title": "Informburo.kz: Cвежие новости Казахстана и мира",
         "content": "30 сентября, 13:55 Женские вагоны и изменение порядка выплат автостраховок. Что ждет казахстанцев в октябре 2026-го?"},
        {"url": "https://local.example.org/article", "title": "ЧП в Каспийском море",
         "content": "В Казахстане расследуют гибель военнослужащих во время учений."},
    ], "unresponsive_engines": []}
    with _http(payload):
        result = tool_web_search(query="новости ЧП Казахстан 3 октября 2026", top_k=1)
    assert result["sources"][0]["url"] == "https://local.example.org/article"


def test_site_path_and_exclusion_on_page_two_are_applied_before_output_limit():
    url = "https://docs.example.org/current/guide"
    payload = {"results": [
        {"url": "https://old.docs.example.org/current/guide", "title": "API guide"},
        {"url": "https://docs.example.org/archive/guide", "title": "API guide"},
        {"url": "https://other.example.org/current/guide", "title": "API guide"},
        {"url": url, "title": "API guide"},
    ], "unresponsive_engines": [["bing news", "parsing error"]]}
    with _http(payload) as calls:
        result = tool_web_search(query="API site:docs.example.org/current/ -site:old.docs.example.org",
                                 page=2, categories="news", top_k=1)
    assert len(calls) == 1 and calls[0]["params"]["pageno"] == "2"
    assert [source["url"] for source in result["sources"]] == [url]
    assert result["engine_warnings"] == [{"engine": "bing news", "error": "parsing error"}]
