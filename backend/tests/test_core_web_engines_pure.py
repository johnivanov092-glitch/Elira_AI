"""Tests for pure helpers in app.core.web_engines.

Constants: SUPPORTED_SEARCH_ENGINES, ENGINE_LABELS, ENGINE_PRIORITY, KZ_LOCAL_NEWS_DOMAINS;
clean_url, extract_domain, domain_matches, resolve_search_engines (no-searxng branch).
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.web_engines import (  # noqa: E402
    SUPPORTED_SEARCH_ENGINES,
    ENGINE_LABELS,
    ENGINE_PRIORITY,
    KZ_LOCAL_NEWS_DOMAINS,
    clean_url,
    extract_domain,
    domain_matches,
    resolve_search_engines,
)


# ---
# core/web_engines.py - Constants
# ---

class WebEngineConstantsTest(unittest.TestCase):
    def test_supported_engines_is_tuple_or_collection(self) -> None:
        self.assertEqual(SUPPORTED_SEARCH_ENGINES, ("searxng",))

    def test_retired_engine_is_not_supported(self) -> None:
        self.assertNotIn("wikipedia", SUPPORTED_SEARCH_ENGINES)

    def test_supported_engines_has_searxng(self) -> None:
        self.assertIn("searxng", SUPPORTED_SEARCH_ENGINES)

    def test_engine_labels_is_dict(self) -> None:
        self.assertIsInstance(ENGINE_LABELS, dict)

    def test_engine_labels_has_duckduckgo(self) -> None:
        self.assertIn("duckduckgo", ENGINE_LABELS)

    def test_engine_labels_has_wikipedia(self) -> None:
        self.assertIn("wikipedia", ENGINE_LABELS)

    def test_engine_labels_values_are_strings(self) -> None:
        for k, v in ENGINE_LABELS.items():
            self.assertIsInstance(v, str, f"Label for {k} is not a string")

    def test_engine_priority_is_dict(self) -> None:
        self.assertIsInstance(ENGINE_PRIORITY, dict)

    def test_engine_priority_searxng_is_lowest_number(self) -> None:
        self.assertEqual(ENGINE_PRIORITY["searxng"], 0)

    def test_kz_local_news_domains_nonempty(self) -> None:
        self.assertGreater(len(KZ_LOCAL_NEWS_DOMAINS), 0)

    def test_kz_local_news_domains_contains_tengri(self) -> None:
        self.assertIn("tengrinews.kz", KZ_LOCAL_NEWS_DOMAINS)


# ---
# core/web_engines.py - clean_url
# ---

class CleanUrlTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(clean_url("https://example.com"), str)

    def test_plain_url_unchanged(self) -> None:
        url = "https://example.com/path"
        self.assertEqual(clean_url(url), url)

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(clean_url(""), "")

    def test_none_returns_empty(self) -> None:
        self.assertEqual(clean_url(None), "")  # type: ignore[arg-type]

    def test_google_redirect_unwrapped(self) -> None:
        google_url = "/url?q=https%3A%2F%2Fexample.com&sa=U"
        result = clean_url(google_url)
        self.assertIn("example.com", result)

    def test_percent_encoded_url_preserved(self) -> None:
        url = "https://example.com/path%20with%20spaces"
        result = clean_url(url)
        self.assertEqual(result, url)

    def test_strips_leading_whitespace(self) -> None:
        result = clean_url("  https://example.com  ")
        self.assertFalse(result.startswith(" "))


# ---
# core/web_engines.py - extract_domain
# ---

class ExtractDomainTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(extract_domain("https://example.com"), str)

    def test_simple_domain(self) -> None:
        self.assertEqual(extract_domain("https://example.com/page"), "example.com")

    def test_www_prefix_stripped(self) -> None:
        self.assertEqual(extract_domain("https://www.example.com"), "example.com")

    def test_subdomain_preserved(self) -> None:
        result = extract_domain("https://news.bbc.co.uk/article")
        self.assertIn("bbc.co.uk", result)

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(extract_domain(""), "")

    def test_returns_lowercase(self) -> None:
        result = extract_domain("https://Example.COM/Path")
        self.assertEqual(result, result.lower())

    def test_with_port_number(self) -> None:
        result = extract_domain("http://localhost:8080/api")
        self.assertIn("localhost", result)

    def test_tengrinews_domain(self) -> None:
        result = extract_domain("https://tengrinews.kz/story/123")
        self.assertEqual(result, "tengrinews.kz")


# ---
# core/web_engines.py - domain_matches
# ---

class DomainMatchesTest(unittest.TestCase):
    def test_returns_bool(self) -> None:
        self.assertIsInstance(domain_matches("example.com", ["example.com"]), bool)

    def test_exact_match(self) -> None:
        self.assertTrue(domain_matches("example.com", ["example.com"]))

    def test_subdomain_match(self) -> None:
        self.assertTrue(domain_matches("news.example.com", ["example.com"]))

    def test_no_match(self) -> None:
        self.assertFalse(domain_matches("other.com", ["example.com"]))

    def test_empty_expected_no_match(self) -> None:
        self.assertFalse(domain_matches("example.com", []))

    def test_multiple_expected_one_matches(self) -> None:
        self.assertTrue(domain_matches("tengrinews.kz", ["nur.kz", "tengrinews.kz"]))

    def test_partial_suffix_not_matched(self) -> None:
        # "notexample.com" should NOT match "example.com"
        self.assertFalse(domain_matches("notexample.com", ["example.com"]))

    def test_kz_domain_in_local_news_list(self) -> None:
        self.assertTrue(domain_matches("tengrinews.kz", KZ_LOCAL_NEWS_DOMAINS))

    def test_foreign_domain_not_in_kz_list(self) -> None:
        self.assertFalse(domain_matches("bbc.co.uk", KZ_LOCAL_NEWS_DOMAINS))


# ---
# core/web_engines.py - re_sub_html
# ---


# ---
# core/web_engines.py - engine_available
# ---


# ---
# core/web_engines.py - resolve_search_engines
# ---

class ResolveSearchEnginesTest(unittest.TestCase):
    def _no_searxng(self):
        """Ensure SEARXNG_URL is absent for deterministic results."""
        return os.environ.pop("SEARXNG_URL", None)

    def _restore(self, old):
        if old is not None:
            os.environ["SEARXNG_URL"] = old

    def test_returns_tuple(self) -> None:
        old = self._no_searxng()
        try:
            self.assertIsInstance(resolve_search_engines(), tuple)
        finally:
            self._restore(old)

    def test_routes_only_to_searxng(self) -> None:
        old = self._no_searxng()
        try:
            self.assertEqual(resolve_search_engines(), ("searxng",))
        finally:
            self._restore(old)

    def test_retired_adapter_not_resolved(self) -> None:
        old = self._no_searxng()
        try:
            self.assertNotIn("wikipedia", resolve_search_engines())
        finally:
            self._restore(old)

    def test_no_duplicates(self) -> None:
        old = self._no_searxng()
        try:
            result = resolve_search_engines()
            self.assertEqual(len(result), len(set(result)))
        finally:
            self._restore(old)

    def test_unknown_engine_filtered_out(self) -> None:
        old = self._no_searxng()
        try:
            result = resolve_search_engines(["nonexistent_engine"])
            self.assertNotIn("nonexistent_engine", result)
        finally:
            self._restore(old)

    def test_legacy_preferences_route_to_sole_backend(self) -> None:
        old = self._no_searxng()
        try:
            result = resolve_search_engines(["wikipedia"])
            self.assertEqual(result, ("searxng",))
        finally:
            self._restore(old)

    def test_none_uses_defaults(self) -> None:
        old = self._no_searxng()
        try:
            result = resolve_search_engines(None)
            self.assertGreater(len(result), 0)
        finally:
            self._restore(old)


if __name__ == "__main__":
    unittest.main()
