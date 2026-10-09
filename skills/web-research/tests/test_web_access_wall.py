"""Login walls and bot checks are failed reads, not excerpts (task 18, 2026-10-06)."""
from __future__ import annotations

import pytest

from webskill.application.code_agent.tools import _web
from webskill.application.code_agent.tools._web import _access_wall
from webskill.infrastructure.search.web_runtime import PageFetchResult

THREAD = "https://old.reddit.com/r/worldwarzthegame/comments/1jw980q/fastest_way_to_level_up_class_level/"


@pytest.mark.parametrize(("final_url", "text", "reason"), [
    ("https://old.reddit.com/login/?reason=lor2&dest=x", "Welcome to Reddit", "вход"),
    (THREAD + "?solution=abc&js_challenge=1&jsc_token=t", "Loading", "проверки от ботов"),
    (THREAD, "Just a moment... Enable JavaScript and cookies to continue", "проверки от ботов"),
    (THREAD, "Проверка, что вы не робот", "проверки от ботов"),
])
def test_walls_are_recognised(final_url, text, reason):
    result = PageFetchResult(text=text, final_url=final_url, status_code=200)
    assert reason in _access_wall(THREAD, result)


@pytest.mark.parametrize(("requested", "final_url", "text"), [
    ("https://nginx.org/en/security_advisories.html", "https://nginx.org/en/security_advisories.html",
     "nginx security advisories. Access denied errors in auth_request were fixed. " * 80),
    ("https://example.org/login", "https://example.org/login", "Login form documentation"),
    ("https://example.org/a", "https://example.org/a", "Statistic: 85% access rate"),
])
def test_ordinary_pages_are_not_walls(requested, final_url, text):
    assert _access_wall(requested, PageFetchResult(text=text, final_url=final_url, status_code=200)) == ""


def test_wall_becomes_a_failed_read(monkeypatch):
    monkeypatch.setattr(_web, "_fetch_one_untracked", lambda url, limit: PageFetchResult(
        text="Welcome to Reddit", final_url="https://old.reddit.com/login/?reason=lor2", status_code=200))
    result = _web._fetch_one(THREAD, 4000, force_refresh=True)
    assert not result.ok and result.text == ""
    assert "не открылась" in result.error and "другой источник" in result.error
