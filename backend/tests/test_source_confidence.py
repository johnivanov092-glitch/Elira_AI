"""Tests for the source-confidence (epistemic) layer in web_runtime.

The classifier is deterministic and code-side: it labels each search result
verified / plausible / unverified. The LLM only narrates these — so this
behaviour must be stable and conservative.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.web_runtime import (  # noqa: E402
    CONFIDENCE_LABELS,
    classify_confidence,
    format_search_results,
    is_trusted_domain,
    parse_result_date,
)

NOW = datetime(2026, 5, 30, tzinfo=timezone.utc)


def _item(href="", date="", title="t", body="b", engine="duckduckgo"):
    return {"href": href, "date": date, "title": title, "body": body, "engine": engine}


# ── is_trusted_domain ────────────────────────────────────────────────────

def test_trusted_reputable_and_special_tlds():
    assert is_trusted_domain("en.wikipedia.org")
    assert is_trusted_domain("reuters.com")
    assert is_trusted_domain("nationalbank.kz")   # finance high-confidence
    assert is_trusted_domain("tengrinews.kz")     # kz local news
    assert is_trusted_domain("nasa.gov")
    assert is_trusted_domain("mit.edu")
    assert is_trusted_domain("hmrc.service.gov.uk")  # ".gov." inside country form


def test_untrusted_domains():
    assert not is_trusted_domain("random-blog-1234.xyz")
    assert not is_trusted_domain("")
    assert not is_trusted_domain("medium.com")


# ── parse_result_date ────────────────────────────────────────────────────

def test_parse_dates_various_formats():
    assert parse_result_date("2026-05-29") is not None
    assert parse_result_date("2026-05-29T10:00:00Z") is not None
    assert parse_result_date("29.05.2026") is not None
    assert parse_result_date("") is None
    assert parse_result_date("not a date") is None


def test_parsed_dates_are_utc_aware():
    dt = parse_result_date("2026-05-29")
    assert dt is not None and dt.tzinfo is not None


# ── classify_confidence ──────────────────────────────────────────────────

def test_verified_trusted_and_fresh():
    item = _item(href="https://reuters.com/x", date="2026-05-20")
    assert classify_confidence(item, all_results=[item], now=NOW) == "verified"


def test_verified_trusted_and_corroborated_without_date():
    a = _item(href="https://reuters.com/x")
    b = _item(href="https://bbc.com/y")
    assert classify_confidence(a, all_results=[a, b], now=NOW) == "verified"


def test_plausible_trusted_single_source_no_date():
    a = _item(href="https://reuters.com/x")
    # only one trusted domain in the set -> not corroborated, no date -> plausible
    assert classify_confidence(a, all_results=[a], now=NOW) == "plausible"


def test_plausible_untrusted_but_dated():
    a = _item(href="https://some-blog.xyz/p", date="2026-05-25")
    assert classify_confidence(a, all_results=[a], now=NOW) == "plausible"


def test_unverified_untrusted_undated_alone():
    a = _item(href="https://some-blog.xyz/p")
    assert classify_confidence(a, all_results=[a], now=NOW) == "unverified"


def test_stale_date_on_trusted_is_not_fresh_but_still_trusted():
    old = (NOW - timedelta(days=400)).strftime("%Y-%m-%d")
    a = _item(href="https://reuters.com/x", date=old)
    # trusted, stale, single source -> plausible (not verified)
    assert classify_confidence(a, all_results=[a], now=NOW) == "plausible"


def test_future_date_not_treated_as_fresh():
    future = (NOW + timedelta(days=10)).strftime("%Y-%m-%d")
    a = _item(href="https://reuters.com/x", date=future)
    # future date fails 0 <= delta, single trusted source -> plausible
    assert classify_confidence(a, all_results=[a], now=NOW) == "plausible"


# ── format_search_results surfaces the label ─────────────────────────────

def test_format_includes_confidence_label():
    items = [
        _item(href="https://reuters.com/x", date="2026-05-29", title="Fresh"),
        _item(href="https://blog.xyz/p", title="Sketchy"),
    ]
    out = format_search_results(items)
    assert CONFIDENCE_LABELS["verified"] in out or CONFIDENCE_LABELS["plausible"] in out
    assert CONFIDENCE_LABELS["unverified"] in out
    assert "Fresh" in out and "Sketchy" in out


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
