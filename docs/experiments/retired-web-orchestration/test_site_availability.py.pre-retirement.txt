"""Demand-driven source backoff, isolated from answer quality and page contents."""
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
import sqlite3

import pytest

from webskill.application.web_evidence import availability as health
from webskill.infrastructure.web_corpus import store
from webskill.application.code_agent.tools import _web
from webskill.infrastructure.search.web_runtime import PageFetchResult

URL = "https://example.org/missing?token=private-value"


@pytest.fixture
def now(monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(store, "_now", lambda: clock[0])
    monkeypatch.setattr(health.time, "time", lambda: clock[0])
    return clock


def row(url=URL, channel="http", origin=False):
    keys = health._identity(url, channel)
    return store.site_access_rows(list(keys[:2])).get(keys[int(origin)], {})


def fail(url=URL, *, status=404, error="", channel="http", force=False, retry_after=""):
    probe = health.begin(url, channel=channel, force=force)
    assert probe["allowed"], probe
    health.finish(probe, ok=False, status=status, error=error, retry_after=retry_after)
    return probe


def test_backoff_is_due_only_on_demand_then_manual_recovery(now):
    for delay in (60, 7 * 86400, 14 * 86400, 28 * 86400):
        fail()
        assert row()["retry_at"] == now[0] + delay
        for _ in range(3):
            assert "blocked" in health.begin(URL)
            assert URL in health.notes([URL])
        old = row()
        now[0] += delay + 1
        # Time passing and search annotations do not dispatch or change history.
        assert health.notes([URL]) == {}
        assert row() == old
    fail()
    assert row()["stopped"] and row()["failures"] == 5
    now[0] += 365 * 86400
    assert "приостановлены" in health.begin(URL)["blocked"]
    probe = health.begin(URL, force=True)
    health.finish(probe, ok=True, status=200)
    assert row()["failures"] == 0 and not row()["stopped"]
    assert health.begin(URL)["allowed"]


def test_missing_page_does_not_block_other_paths_or_browser(now):
    fail()
    assert health.begin("https://example.org/working")["allowed"]
    assert health.begin(URL, channel="browser")["allowed"]
    assert "blocked" in health.begin(URL + "#section")


def test_origin_failure_then_missing_path_does_not_poison_origin(now):
    fail(status=None, error="Name resolution failed")
    assert "blocked" in health.begin("https://example.org/other")
    now[0] += 61
    fail(status=404)
    assert row(origin=True)["failures"] == 0
    assert row()["failures"] == 1
    assert health.begin("https://example.org/working")["allowed"]


def test_success_clears_path_and_origin_history(now):
    fail()
    now[0] += 61
    fail(status=None, error="connect timeout")
    now[0] += 61
    probe = health.begin(URL)
    health.finish(probe, ok=False, status=200, error="unsupported format")
    assert row()["failures"] == row(origin=True)["failures"] == 0


@pytest.mark.parametrize("date_header", [False, True])
def test_rate_limit_honors_retry_after_and_never_marks_site_dead(now, date_header):
    for _ in range(7):
        header = formatdate(now[0] + 120, usegmt=True) if date_header else "120"
        fail(status=429, retry_after=header)
        assert row(origin=True)["retry_at"] == now[0] + 120
        assert not row(origin=True)["stopped"]
        now[0] += 121
    fail(status=None, error="Name resolution failed")
    assert row(origin=True)["failures"] == 1


@pytest.mark.parametrize("error", ["cancelled", "SSRF denied", "invalid URL", "fragment missing", "unsupported format"])
def test_unknown_local_and_cancelled_errors_do_not_teach_failure(now, error):
    fail(status=None, error=error)
    assert row()["failures"] == 0
    assert health.begin(URL)["allowed"]


def test_concurrent_due_probe_and_stale_completion(now):
    with ThreadPoolExecutor(max_workers=5) as pool:
        probes = list(pool.map(health.begin, [URL] * 5))
    assert sum(bool(p.get("allowed")) for p in probes) == 1
    old = next(p for p in probes if p["allowed"])
    now[0] += 301
    fresh = health.begin(URL)
    health.finish(old, ok=False, status=404)
    assert row()["failures"] == 0
    health.finish(fresh, ok=True)
    assert health.begin(URL)["allowed"]


def test_parallel_origin_failures_cannot_skip_weekly_stages(now):
    probes = [health.begin(f"https://example.org/{n}") for n in range(5)]
    for probe in probes:
        health.finish(probe, ok=False, error="connection refused")
    assert row(origin=True)["failures"] == 1


def test_late_path_failure_cannot_overwrite_newer_origin_probe(now):
    first, late = health.begin(URL), health.begin("https://example.org/slow")
    health.finish(first, ok=False, error="connection refused")
    now[0] += 61
    fresh = health.begin("https://example.org/probe")
    health.finish(late, ok=False, error="connection refused")
    assert row(origin=True)["failures"] == 1
    health.finish(fresh, ok=True, status=200)
    assert row(origin=True)["failures"] == 0


def test_parallel_missing_page_does_not_reset_rate_limit(now):
    first, other = health.begin(URL), health.begin("https://example.org/other")
    health.finish(first, ok=False, status=429, retry_after="86400")
    health.finish(other, ok=False, status=404)
    assert row(origin=True)["retry_at"] == now[0] + 86400
    assert "blocked" in health.begin("https://example.org/third")


def test_store_contains_no_query_path_or_document(now):
    fail()
    with sqlite3.connect(store._db_path()) as db:
        saved = repr(db.execute("SELECT * FROM site_availability").fetchall())
    assert "example.org" in saved
    assert "missing" not in saved and "private-value" not in saved and "token=" not in saved


def test_store_outage_does_not_disable_reading(monkeypatch):
    def unavailable(*args, **kwargs):
        raise store.StoreUnavailable("unavailable")
    monkeypatch.setattr(store, "_connect", unavailable)
    assert health.begin(URL) == {}
    assert health.notes([URL]) == {}


def test_no_memory_policy_does_not_write_history(monkeypatch):
    from app.application.code_agent import loop_helpers
    from app.application.code_agent.tools._shell import _CURRENT_RUN_ID
    monkeypatch.setattr(loop_helpers, "run_persistence_policy", lambda _: loop_helpers.task_persistence_policy(auto_remember=False))
    token = _CURRENT_RUN_ID.set("no-memory")
    try:
        assert health.begin(URL) == {}
        assert health.notes([URL]) == {}
    finally:
        _CURRENT_RUN_ID.reset(token)
    assert row() == {}


def test_fetch_cooldown_avoids_network_then_force_refresh_resets(now, monkeypatch):
    calls = []
    def fetch(url, limit):
        calls.append(url)
        return PageFetchResult(final_url=url, status_code=404, error="HTTP 404") if len(calls) == 1 else PageFetchResult(text="source", status_code=200)
    monkeypatch.setattr(_web, "_fetch_one_untracked", fetch)
    assert not _web.tool_web_fetch(url=URL)["ok"]
    assert not _web.tool_web_fetch(url=URL)["ok"]
    assert len(calls) == 1
    assert _web.tool_web_fetch(url=URL, force_refresh=True)["ok"]
    assert len(calls) == 2 and row()["failures"] == 0


def test_passive_browser_cooldown_does_not_block_interaction(now, monkeypatch):
    monkeypatch.setattr("webskill.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    calls = []
    def render(*args):
        calls.append(args)
        if not args[3]:
            raise TimeoutError("navigation timeout")
        return "page", URL, "Clicked successfully", 1, None, 200
    monkeypatch.setattr(_web, "_browser_render", render)
    assert not _web.tool_browser(url=URL)["ok"]
    assert _web.tool_browser(url=URL)["error"] == "source_cooldown"
    assert _web.tool_browser(url=URL, actions=[{"click": "Next"}])["ok"]
    assert len(calls) == 2


def test_browser_retry_after_and_fallback_share_channel(now, monkeypatch):
    monkeypatch.setattr("webskill.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    monkeypatch.setattr(_web, "_browser_render", lambda *a: ("", URL, "", 0, None, 429, "86400"))
    assert not _web.tool_browser(url=URL)["ok"]
    assert row(channel="browser", origin=True)["retry_at"] == now[0] + 86400
    monkeypatch.setattr(_web, "_browser_render", lambda *a: pytest.fail("Cooldown must cover fallback too"))
    assert not _web._render_fallback(URL, 500).ok
    assert health.begin(URL)["allowed"]


@pytest.mark.parametrize("status,error", [(429, "HTTP 429"), (None, "Name resolution failed")])
def test_external_redirect_failure_does_not_block_original_origin(now, monkeypatch, status, error):
    monkeypatch.setattr(_web, "_fetch_one_untracked", lambda *a: PageFetchResult(
        final_url="https://external.org/unavailable", status_code=status, error=error, retry_after="86400"))
    assert not _web.tool_web_fetch(url=URL)["ok"]
    assert row()["failures"] == 1
    assert row(origin=True) == {}
    assert health.begin("https://example.org/healthy")["allowed"]


def test_access_wall_pauses_only_that_page_and_is_rechecked_later(now, monkeypatch):
    thread = "https://forum.example.net/t/worldwarz/abc/levels/"
    monkeypatch.setattr(_web, "_fetch_one_untracked", lambda url, limit: PageFetchResult(
        text="Welcome", final_url="https://forum.example.net/login/?next=abc", status_code=200))
    first = _web._fetch_one(thread, 4000)
    assert not first.ok and "не открылась" in first.error
    assert row(thread)["reason"] == "access_wall" and row(thread)["retry_at"] == now[0] + 60
    assert not row(thread, origin=True)  # the site itself is not condemned
    assert health.begin("https://forum.example.net/t/other/")["allowed"]
    blocked = health.begin(thread)
    assert "стены входа или проверки от ботов" in blocked["blocked"]
    now[0] += 61
    _web._fetch_one(thread, 4000)  # due again: rechecked, still a wall → next pause is a week
    assert row(thread)["failures"] == 2 and row(thread)["retry_at"] == now[0] + 7 * 86400


def test_walls_on_two_pages_pause_the_whole_site(now, monkeypatch):
    monkeypatch.setattr(_web, "_fetch_one_untracked", lambda url, limit: PageFetchResult(
        text="Welcome", final_url="https://forum.example.net/login/?next=x", status_code=200))
    _web._fetch_one("https://forum.example.net/t/first/", 4000)
    assert not row("https://forum.example.net/t/first/", origin=True)
    _web._fetch_one("https://forum.example.net/t/second/", 4000)
    site = row("https://forum.example.net/t/second/", origin=True)
    assert site["reason"] == "access_wall" and site["retry_at"] > now[0]
    other = "https://forum.example.net/t/third/"
    assert "пропущен" in health.begin(other)["blocked"]
    assert other in health.notes([other])  # search results mark the whole site


def test_render_failure_is_a_failed_page_not_an_unknown_error(now):
    fail(status=None, error="browser fallback failed: Page.content: Unable to retrieve content "
                            "because the page is navigating and changing the content.")
    assert row()["reason"] == "render_failure" and row()["failures"] == 1
    assert "ошибки отрисовки страницы" in health.begin(URL)["blocked"]


def test_reddit_is_neither_searched_nor_read(monkeypatch):
    calls = []
    monkeypatch.setattr(_web, "_fetch_one_untracked", lambda url, limit: calls.append(url))
    result = _web._fetch_one("https://www.reddit.com/r/worldwarzthegame/comments/abc/levels/", 4000)
    assert not result.ok and "reddit.com не читается" in result.error and calls == []

    monkeypatch.setattr("webskill.infrastructure.search.web_search.search_web", lambda *a, **kw: {"sources": [
        {"title": "thread", "href": "https://www.reddit.com/r/x/comments/1/"},
        {"title": "docs", "href": "https://docs.example.org/page"},
    ]})
    found = _web._run_search("query", 5, "", "")
    assert [item["href"] for item in found] == ["https://docs.example.org/page"]
