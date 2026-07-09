"""W6 source tiers — a deterministic heuristic-GUARD (contract schema: tier enum
official/primary/secondary/ugc/unknown).

The tier says what KIND of publisher a URL belongs to, from a small curated list
plus structural rules — nothing model-driven, nothing network. It is an
annotation to help weigh sources (invariant №9: an official primary source may
suffice alone), NEVER a truth verdict and NEVER a substitute for provenance.
Anything not confidently matched stays "unknown" — we do not guess.

  official  — standards bodies, government TLDs, vendor documentation hosts
  primary   — package/code registries and primary-literature archives
  secondary — encyclopedias/aggregators explicitly known as secondary
  ugc       — user-generated content (forums, Q&A, social, blogs platforms)
  unknown   — everything else (the honest default)
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.application.web_evidence.freshness import registrable_domain

# Curated registrable-domain lists — deliberately SMALL and obvious. Growing them
# is cheap; guessing is not allowed (unknown is the honest default).
_OFFICIAL_DOMAINS = frozenset({
    "w3.org", "ietf.org", "rfc-editor.org", "iso.org", "iec.ch", "ecma-international.org",
    "python.org", "kernel.org", "postgresql.org", "sqlite.org", "openssl.org",
    "mozilla.org", "rust-lang.org", "golang.org", "go.dev", "nodejs.org", "php.net",
    "oracle.com", "microsoft.com", "apple.com",
})
_PRIMARY_DOMAINS = frozenset({
    "pypi.org", "npmjs.com", "crates.io", "rubygems.org", "packagist.org", "nuget.org",
    "hub.docker.com", "github.com", "gitlab.com", "bitbucket.org", "sr.ht",
    "arxiv.org", "doi.org", "pubmed.ncbi.nlm.nih.gov",
})
_SECONDARY_DOMAINS = frozenset({
    "wikipedia.org", "wikidata.org", "britannica.com",
})
_UGC_DOMAINS = frozenset({
    "stackoverflow.com", "stackexchange.com", "superuser.com", "serverfault.com",
    "askubuntu.com", "reddit.com", "news.ycombinator.com", "quora.com",
    "habr.com", "medium.com", "dev.to", "hashnode.dev", "substack.com",
    "twitter.com", "x.com", "facebook.com", "vk.com", "t.me", "youtube.com",
    "blogspot.com", "wordpress.com", "livejournal.com",
})
# Documentation-style hosts: docs.<vendor>, developer.<vendor> etc. — the vendor's
# own docs are official for that vendor's subject matter.
_DOC_HOST_PREFIXES = ("docs.", "developer.", "developers.", "devdocs.", "learn.", "wiki.")
_GOV_SUFFIXES = (".gov", ".mil", ".gov.uk", ".gov.au", ".gov.ru", ".gc.ca", ".europa.eu")

TIERS = ("official", "primary", "secondary", "ugc", "unknown")


def classify_tier(url: str) -> str:
    """Deterministic tier for a URL. Curated lists + structural rules; the
    default is 'unknown' — never a guess."""
    host = (urlparse(url or "").hostname or "").lower().strip(".")
    if not host:
        return "unknown"
    dom = registrable_domain(url)
    if host.endswith(_GOV_SUFFIXES):
        return "official"
    if dom in _UGC_DOMAINS:
        return "ugc"
    if dom in _SECONDARY_DOMAINS:
        return "secondary"
    if dom in _PRIMARY_DOMAINS:
        return "primary"
    if dom in _OFFICIAL_DOMAINS:
        return "official"
    if host.startswith(_DOC_HOST_PREFIXES):
        return "official"      # vendor documentation host (structural rule)
    return "unknown"


def tier_note(tier: str) -> str:
    """Short RU annotation used in search output and the ledger render."""
    return {
        "official": "official — вендор/стандарт/гос",
        "primary": "primary — реестр/первоисточник",
        "secondary": "secondary — энциклопедия/агрегатор",
        "ugc": "UGC — форум/соцсеть, перепроверяй",
    }.get(tier, "")
